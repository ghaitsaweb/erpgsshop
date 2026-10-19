from core.models import Company

from .context import reset_current_context, resolve_context, set_current_context
from .models import Branch


class OrganizationContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        context = None
        token = None
        if getattr(request, "user", None) and request.user.is_authenticated:
            context = resolve_context(request.user, request.session)
            request.organization_context = context
            request.user._gsshop_allowed_company_ids = context.allowed_company_ids
            request.user._gsshop_allowed_branch_ids = context.allowed_branch_ids
            companies_by_id = Company.objects.in_bulk(context.active_company_ids)
            branches_by_id = Branch.objects.select_related("company").in_bulk(context.active_branch_ids)
            request.active_companies = [
                companies_by_id[company_id]
                for company_id in context.active_company_ids
                if company_id in companies_by_id
            ]
            request.active_branches = [
                branches_by_id[branch_id]
                for branch_id in context.active_branch_ids
                if branch_id in branches_by_id
            ]
            request.active_company = companies_by_id.get(context.company_id)
            request.active_branch = branches_by_id.get(context.branch_id)
            token = set_current_context(context)
        else:
            request.organization_context = None
            request.active_company = None
            request.active_branch = None
            request.active_companies = []
            request.active_branches = []

        try:
            return self.get_response(request)
        finally:
            if token is not None:
                reset_current_context(token)
