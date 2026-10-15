import os
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Max, Min
from django.utils import timezone

from core.models import (
    Attendance,
    Company,
    Contact,
    Employee,
    Invoice,
    InvoiceLine,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    SaleOrder,
    SaleOrderLine,
    StockMove,
    Warehouse,
)
from modules.ai_workspace.models import AIAgent, AIWorkspace
from modules.crm.models import Lead, Opportunity
from modules.education.models import Course, Enrollment, LibraryBook, School, Student
from modules.finance.models import Account, Budget
from modules.hr.models import (
    Department,
    EmploymentContract,
    LeaveRequest,
    Payslip,
    TrainingConduct,
    TrainingConductParticipant,
    TrainingConductSchedule,
    TrainingCourse,
    TrainingTrainer,
)
from modules.operations.models import (
    Asset,
    BillOfMaterial,
    BillOfMaterialLine,
    ConstructionProject,
    ManufacturingOrder,
    Project,
    QualityInspection,
)
from modules.pos.models import POSOrder, POSOrderLine, POSRegister, POSSession, RestaurantTable


class Command(BaseCommand):
    help = "Seed a small idempotent GSSHOP dataset for local evaluation."

    @transaction.atomic
    def handle(self, *args, **options):
        if os.getenv("SEED_DEMO", "False").lower() != "true":
            self.stdout.write("Demo seed skipped.")
            return

        company, _ = Company.objects.get_or_create(
            code="GSS",
            defaults={
                "name": "GSSHOP Indonesia",
                "email": "hello@gsshop.local",
                "phone": "+62 21 555 0101",
                "address": "Jakarta, Indonesia",
            },
        )
        customer, _ = Contact.objects.get_or_create(
            company=company,
            code="CUST-001",
            defaults={"name": "Nusantara Retail", "contact_type": Contact.ContactType.CUSTOMER, "email": "buyer@example.local"},
        )
        vendor, _ = Contact.objects.get_or_create(
            company=company,
            code="VEND-001",
            defaults={"name": "Sumber Makmur", "contact_type": Contact.ContactType.VENDOR, "email": "vendor@example.local"},
        )
        warehouse, _ = Warehouse.objects.get_or_create(
            company=company,
            code="WH-JKT",
            defaults={"name": "Jakarta Main Warehouse", "address": "Jakarta"},
        )
        products = []
        for sku, name, sale, purchase, reorder in [
            ("GS-1001", "GSSHOP Everyday Tote", "189000", "92000", "10"),
            ("GS-1002", "GSSHOP Travel Bottle", "149000", "68000", "12"),
            ("GS-1003", "GSSHOP Desk Organizer", "229000", "110000", "8"),
        ]:
            product, _ = Product.objects.get_or_create(
                company=company,
                sku=sku,
                defaults={
                    "name": name,
                    "sale_price": Decimal(sale),
                    "purchase_price": Decimal(purchase),
                    "reorder_level": Decimal(reorder),
                },
            )
            products.append(product)

        username = os.getenv("DJANGO_SUPERUSER_USERNAME", "admin")
        user = get_user_model().objects.filter(username=username).first()
        if user:
            for product in products:
                reference = f"OPENING-{product.sku}"
                if not StockMove.objects.filter(reference=reference, product=product).exists():
                    StockMove.objects.create(
                        company=company,
                        product=product,
                        warehouse=warehouse,
                        direction=StockMove.Direction.IN,
                        quantity=Decimal("25"),
                        reference=reference,
                        note="Opening balance",
                        created_by=user,
                    )

        sale, created = SaleOrder.objects.get_or_create(
            company=company,
            number="SO-2026-0001",
            defaults={"customer": customer, "status": SaleOrder.Status.CONFIRMED},
        )
        if created:
            SaleOrderLine.objects.create(order=sale, product=products[0], description=products[0].name, quantity=2, unit_price=products[0].sale_price)
            SaleOrderLine.objects.create(order=sale, product=products[1], description=products[1].name, quantity=1, unit_price=products[1].sale_price)

        purchase, created = PurchaseOrder.objects.get_or_create(
            company=company,
            number="PO-2026-0001",
            defaults={"vendor": vendor, "status": PurchaseOrder.Status.CONFIRMED},
        )
        if created:
            PurchaseOrderLine.objects.create(order=purchase, product=products[2], description=products[2].name, quantity=10, unit_price=products[2].purchase_price)

        invoice, created = Invoice.objects.get_or_create(
            company=company,
            number="INV-2026-0001",
            defaults={
                "invoice_type": Invoice.InvoiceType.CUSTOMER,
                "partner": customer,
                "due_date": timezone.localdate() + timedelta(days=14),
                "status": Invoice.Status.POSTED,
            },
        )
        if created:
            InvoiceLine.objects.create(invoice=invoice, product=products[0], description=products[0].name, quantity=2, unit_price=products[0].sale_price)

        employee, _ = Employee.objects.get_or_create(
            company=company,
            employee_id="EMP-001",
            defaults={"name": "Ayu Pratama", "email": "ayu@gsshop.local", "department": "Operations", "position": "Operations Lead"},
        )
        Attendance.objects.get_or_create(
            employee=employee,
            attendance_date=timezone.localdate(),
            defaults={"status": Attendance.Status.PRESENT},
        )

        leadership_course, _ = TrainingCourse.objects.get_or_create(
            company=company,
            code="TRN-LEAD",
            defaults={"name": "Leadership Foundations", "category": "Leadership", "duration_hours": Decimal("16")},
        )
        reporting_course, _ = TrainingCourse.objects.get_or_create(
            company=company,
            code="TRN-EXCEL",
            defaults={"name": "Excel Reporting for Operations", "category": "Productivity", "duration_hours": Decimal("8")},
        )
        trainer, _ = TrainingTrainer.objects.get_or_create(
            company=company,
            name="Nadia Wirawan",
            defaults={"email": "nadia.trainer@gsshop.local", "specialties": "Leadership and operational reporting"},
        )
        internal_conduct, _ = TrainingConduct.objects.get_or_create(
            reference="TRN/INT/0001",
            defaults={
                "company": company,
                "name": "Leadership Foundations Batch",
                "training_type": TrainingConduct.TrainingType.INTERNAL,
                "training_source": TrainingConduct.TrainingSource.COURSE,
                "course": leadership_course,
                "pic": employee,
                "trainer": trainer,
                "location": "GSSHOP Learning Room",
            },
        )
        TrainingConductSchedule.objects.get_or_create(
            conduct=internal_conduct,
            sequence=1,
            defaults={
                "course": leadership_course,
                "trainer": trainer,
                "start_date": timezone.localdate() + timedelta(days=7),
                "end_date": timezone.localdate() + timedelta(days=8),
                "start_time": "09:00",
                "end_time": "16:00",
            },
        )
        TrainingConductParticipant.objects.get_or_create(conduct=internal_conduct, employee=employee)
        public_conduct, _ = TrainingConduct.objects.get_or_create(
            reference="TRN/PUB/0002",
            defaults={
                "company": company,
                "name": "Excel Reporting for Operations Batch",
                "training_type": TrainingConduct.TrainingType.PUBLIC,
                "training_source": TrainingConduct.TrainingSource.COURSE,
                "course": reporting_course,
                "pic": employee,
                "trainer": trainer,
                "location": "Jakarta Training Center",
                "status": TrainingConduct.Status.OPEN,
                "use_minimum_participant": True,
                "minimum_participants": 1,
                "use_maximum_participant": True,
                "maximum_participants": 20,
            },
        )
        TrainingConductSchedule.objects.get_or_create(
            conduct=public_conduct,
            sequence=1,
            defaults={
                "course": reporting_course,
                "trainer": trainer,
                "start_date": timezone.localdate() + timedelta(days=14),
                "end_date": timezone.localdate() + timedelta(days=14),
                "start_time": "09:00",
                "end_time": "17:00",
            },
        )
        TrainingConductParticipant.objects.get_or_create(conduct=public_conduct, employee=employee)
        for conduct in (internal_conduct, public_conduct):
            dates = conduct.schedule_lines.aggregate(start=Min("start_date"), end=Max("end_date"))
            TrainingConduct.objects.filter(pk=conduct.pk).update(
                date_start=dates["start"],
                date_end=dates["end"],
                participant_count=conduct.participant_lines.exclude(status=TrainingConductParticipant.Status.CANCELLED).count(),
            )

        Department.objects.get_or_create(company=company, code="OPS", defaults={"name": "Operations", "manager": employee})
        EmploymentContract.objects.get_or_create(
            number="CTR-EMP-001",
            defaults={
                "employee": employee,
                "contract_type": EmploymentContract.ContractType.PERMANENT,
                "start_date": timezone.localdate() - timedelta(days=365),
                "base_salary": Decimal("8500000"),
                "status": EmploymentContract.Status.ACTIVE,
            },
        )
        LeaveRequest.objects.get_or_create(
            employee=employee,
            start_date=timezone.localdate() + timedelta(days=14),
            defaults={
                "end_date": timezone.localdate() + timedelta(days=15),
                "days": Decimal("2"),
                "reason": "Family event",
            },
        )
        Payslip.objects.get_or_create(
            number="PAY-2026-0001",
            defaults={
                "employee": employee,
                "period_start": timezone.localdate().replace(day=1),
                "period_end": timezone.localdate(),
                "basic_salary": Decimal("8500000"),
                "allowances": Decimal("750000"),
                "deductions": Decimal("250000"),
            },
        )

        Account.objects.get_or_create(
            company=company,
            code="1100",
            defaults={"name": "Cash and Bank", "account_type": Account.AccountType.ASSET},
        )
        Account.objects.get_or_create(
            company=company,
            code="4100",
            defaults={"name": "Sales Revenue", "account_type": Account.AccountType.INCOME},
        )
        Budget.objects.get_or_create(
            company=company,
            name="Operations Budget 2026",
            defaults={
                "start_date": timezone.localdate().replace(month=1, day=1),
                "end_date": timezone.localdate().replace(month=12, day=31),
                "amount": Decimal("1200000000"),
            },
        )

        lead, _ = Lead.objects.get_or_create(
            company=company,
            name="Raka Santoso",
            defaults={
                "organization": "Ruang Niaga",
                "email": "raka@example.local",
                "source": "Website",
                "expected_revenue": Decimal("25000000"),
                "assigned_to": user,
            },
        )
        Opportunity.objects.get_or_create(
            company=company,
            name="Retail rollout package",
            defaults={
                "lead": lead,
                "customer": customer,
                "value": Decimal("25000000"),
                "probability": 60,
                "owner": user,
            },
        )

        bom, bom_created = BillOfMaterial.objects.get_or_create(
            company=company,
            code="BOM-GS-1001",
            defaults={"product": products[0], "output_quantity": Decimal("1")},
        )
        if bom_created:
            BillOfMaterialLine.objects.create(bom=bom, component=products[2], quantity=Decimal("0.25"), warehouse=warehouse)
        ManufacturingOrder.objects.get_or_create(
            company=company,
            number="MO-2026-0001",
            defaults={
                "bom": bom,
                "quantity": Decimal("20"),
                "warehouse": warehouse,
                "planned_start": timezone.now() + timedelta(days=1),
                "planned_end": timezone.now() + timedelta(days=3),
                "responsible": user,
            },
        )
        QualityInspection.objects.get_or_create(
            company=company,
            number="QI-2026-0001",
            defaults={"product": products[0], "reference": "MO-2026-0001", "inspector": user},
        )
        Project.objects.get_or_create(
            company=company,
            code="PRJ-001",
            defaults={
                "name": "GSSHOP Flagship Launch",
                "customer": customer,
                "manager": user,
                "start_date": timezone.localdate(),
                "budget": Decimal("350000000"),
                "progress": 25,
            },
        )
        Asset.objects.get_or_create(
            company=company,
            code="AST-001",
            defaults={
                "name": "POS Terminal Jakarta",
                "product": products[2],
                "purchase_date": timezone.localdate(),
                "acquisition_value": Decimal("12500000"),
                "assigned_employee": employee,
                "location": "Jakarta Store",
            },
        )
        ConstructionProject.objects.get_or_create(
            company=company,
            code="CONST-001",
            defaults={
                "name": "GSSHOP Distribution Center",
                "customer": customer,
                "site_address": "Bekasi, Indonesia",
                "contract_value": Decimal("4500000000"),
                "start_date": timezone.localdate(),
                "progress": 10,
            },
        )

        school, _ = School.objects.get_or_create(company=company, code="GSS-ACADEMY", defaults={"name": "GSSHOP Academy"})
        student, _ = Student.objects.get_or_create(
            school=school,
            student_number="STU-001",
            defaults={"name": "Nadia Putri", "email": "nadia@example.local"},
        )
        course, _ = Course.objects.get_or_create(
            school=school,
            code="ERP-101",
            defaults={"title": "ERP Operations Fundamentals", "instructor": user},
        )
        Enrollment.objects.get_or_create(student=student, course=course, defaults={"progress": 35})
        LibraryBook.objects.get_or_create(
            school=school,
            title="Practical Operations Management",
            defaults={"author": "GSSHOP Academy", "copies_total": 5, "copies_available": 5},
        )

        register, _ = POSRegister.objects.get_or_create(
            company=company,
            code="POS-JKT-01",
            defaults={"name": "Jakarta Store Register", "warehouse": warehouse},
        )
        session = POSSession.objects.filter(register=register, status=POSSession.Status.OPEN).first()
        if not session:
            session = POSSession.objects.create(register=register, opened_by=user, opening_cash=Decimal("1000000"))
        table, _ = RestaurantTable.objects.get_or_create(register=register, name="Table 01", defaults={"area": "Main Hall", "seats": 4})
        pos_order, pos_created = POSOrder.objects.get_or_create(
            number="POS-2026-0001",
            defaults={
                "session": session,
                "customer": customer,
                "order_type": POSOrder.OrderType.DINE_IN,
                "restaurant_table": table,
                "payment_method": "QRIS",
            },
        )
        if pos_created:
            POSOrderLine.objects.create(order=pos_order, product=products[1], quantity=Decimal("2"), unit_price=products[1].sale_price)

        workspace, _ = AIWorkspace.objects.get_or_create(company=company, name="GSSHOP Operations")
        AIAgent.objects.get_or_create(
            workspace=workspace,
            name="Operations Copilot",
            defaults={
                "role": "Assist ERP operators with summaries and follow-up tasks",
                "model_name": "configure-provider-model",
                "allowed_tools": ["sales.summary", "inventory.reorder", "project.status"],
            },
        )
        self.stdout.write(self.style.SUCCESS("GSSHOP demo data is ready."))
