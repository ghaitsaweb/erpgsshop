"""Organization-chart read model and guarded reporting-line updates."""

from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Employee
from modules.organization.context import scope_queryset as scope_organization_queryset

from .access import can_hr_entity_action, scope_hr_queryset
from .models import HRWorkflowAudit


def can_manage_organization_chart(user):
    """A chart move is the same privileged operation as editing an employee."""
    return user.has_perm("core.change_employee") and can_hr_entity_action(user, "employees", "change")


def _visible_employees(user):
    queryset = Employee.objects.filter(status=Employee.Status.ACTIVE).select_related(
        "company", "supervisor", "department_record", "job_position"
    )
    queryset = scope_hr_queryset(user, "employees", queryset)
    return scope_organization_queryset(queryset, include_branch=True).order_by("company__name", "name", "pk")


def _chart_node(employee, children_by_manager, rendered, ancestry=()):
    """Make a serializable tree and remain readable if legacy data contains a cycle."""
    if employee.pk in ancestry:
        return None
    rendered.add(employee.pk)
    children = []
    for child in children_by_manager.get(employee.pk, []):
        child_node = _chart_node(child, children_by_manager, rendered, (*ancestry, employee.pk))
        if child_node is not None:
            children.append(child_node)
    return {"employee": employee, "children": children}


def organization_chart_context(user):
    """Return a company-aware forest for the interactive chart template."""
    employees = list(_visible_employees(user))
    visible_ids = {employee.pk for employee in employees}
    children_by_manager = defaultdict(list)
    roots_by_company = defaultdict(list)

    for employee in employees:
        if employee.supervisor_id in visible_ids and employee.supervisor.company_id == employee.company_id:
            children_by_manager[employee.supervisor_id].append(employee)
        else:
            roots_by_company[employee.company_id].append(employee)

    for children in children_by_manager.values():
        children.sort(key=lambda item: (item.name.casefold(), item.pk))
    for roots in roots_by_company.values():
        roots.sort(key=lambda item: (item.name.casefold(), item.pk))

    rendered = set()
    groups = []
    companies = {}
    for employee in employees:
        companies[employee.company_id] = employee.company
    for company_id, company in sorted(companies.items(), key=lambda item: (item[1].name.casefold(), item[0])):
        roots = []
        for employee in roots_by_company.get(company_id, []):
            if employee.pk not in rendered:
                roots.append(_chart_node(employee, children_by_manager, rendered))
        # A malformed legacy cycle has no natural root. Render it once so an HR
        # manager can repair it by dragging a member onto Top level.
        for employee in employees:
            if employee.company_id == company_id and employee.pk not in rendered:
                roots.append(_chart_node(employee, children_by_manager, rendered))
        groups.append({"company": company, "roots": roots})

    return {
        "chart_groups": groups,
        "chart_employees": employees,
        "chart_employee_count": len(employees),
        "can_manage_chart": can_manage_organization_chart(user),
    }


def reassign_chart_supervisor(user, employee_id, supervisor_id):
    """Assign one visible employee to a visible manager, with cycle protection."""
    if not can_manage_organization_chart(user):
        raise PermissionError("You do not have permission to change reporting lines.")

    try:
        employee_id = int(employee_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Choose a valid employee to move.") from exc
    try:
        supervisor_id = int(supervisor_id) if supervisor_id not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ValidationError("Choose a valid manager.") from exc

    with transaction.atomic():
        # Lock Employee rows only. The chart's display query joins nullable
        # relations (notably supervisor), and PostgreSQL cannot lock the
        # nullable side of that outer join.
        visible = _visible_employees(user).select_related(None).select_for_update()
        try:
            employee = visible.get(pk=employee_id)
        except Employee.DoesNotExist as exc:
            raise ValidationError("The employee is outside your current organization scope.") from exc

        manager = None
        if supervisor_id is not None:
            try:
                manager = visible.get(pk=supervisor_id)
            except Employee.DoesNotExist as exc:
                raise ValidationError("The selected manager is outside your current organization scope.") from exc
            if manager.pk == employee.pk:
                raise ValidationError("An employee cannot report to themselves.")
            if manager.company_id != employee.company_id:
                raise ValidationError("A manager must belong to the same company as the employee.")

            # The moved employee and destination manager are locked above.
            # Reading the company graph without locking every employee keeps a
            # chart move responsive even for a large organization.
            reporting_lines = dict(
                Employee.objects.filter(company_id=employee.company_id).values_list("pk", "supervisor_id")
            )
            ancestor_id = manager.pk
            visited = set()
            while ancestor_id:
                if ancestor_id == employee.pk:
                    raise ValidationError("That move would create a circular reporting line.")
                if ancestor_id in visited:
                    raise ValidationError("The selected manager already belongs to an invalid circular reporting line.")
                visited.add(ancestor_id)
                ancestor_id = reporting_lines.get(ancestor_id)

        previous_manager = employee.supervisor
        if employee.supervisor_id == (manager.pk if manager else None):
            return employee, previous_manager, manager, False

        employee.supervisor = manager
        employee.clean()
        employee.save(update_fields=["supervisor", "updated_at"])
        HRWorkflowAudit.objects.create(
            company_id=employee.company_id,
            entity="employees",
            record_id=employee.pk,
            action="organization_chart_reassign",
            previous_status=employee.status,
            new_status=employee.status,
            actor=user,
            details={
                "source": "organization_chart",
                "previous_supervisor_id": previous_manager.pk if previous_manager else None,
                "previous_supervisor_name": previous_manager.name if previous_manager else "",
                "supervisor_id": manager.pk if manager else None,
                "supervisor_name": manager.name if manager else "",
            },
        )
    return employee, previous_manager, manager, True
