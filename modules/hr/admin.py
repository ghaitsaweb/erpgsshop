from django.contrib import admin

from . import models as hr_models
from .access import FULL_HR_GROUPS
from .forms import EmployeeDocumentForm


for model in [
    hr_models.Department,
    hr_models.EmploymentContract,
    hr_models.OvertimeRequest,
    hr_models.Candidate,
    hr_models.Appraisal,
    hr_models.BusinessUnit,
    hr_models.WorkLocation,
    hr_models.JobPosition,
    hr_models.EmployeeGrade,
    hr_models.Skill,
    hr_models.DocumentType,
    hr_models.HRYear,
    hr_models.CompanyHoliday,
    hr_models.EmployeeChangeRequest,
    hr_models.EmployeeDisciplinary,
    hr_models.EmployeeChecklist,
    hr_models.RawAttendance,
    hr_models.Shift,
    hr_models.WorkSchedule,
    hr_models.ScheduleAssignment,
    hr_models.LeaveType,
    hr_models.LeaveBlackoutPeriod,
    hr_models.OvertimeRule,
    hr_models.PayrollPeriod,
    hr_models.SalaryComponent,
    hr_models.TravelRequest,
    hr_models.TrainingProgram,
    hr_models.HRSetting,
    hr_models.LoanType,
    hr_models.LoanPolicy,
    hr_models.CareerTransitionType,
    hr_models.CareerTransitionLetter,
    hr_models.CareerTransitionRequest,
    hr_models.TravelPackage,
    hr_models.TrainingCourse,
    hr_models.TrainingTrainer,
    hr_models.TrainingConduct,
    hr_models.TrainingConductSchedule,
    hr_models.TrainingConductParticipant,
    hr_models.ExpenseProduct,
    hr_models.CompensationBenefit,
    hr_models.CompensationBenefitLine,
    hr_models.JobClassification,
    hr_models.JobExperienceLevel,
    hr_models.EmployeeCategory,
    hr_models.EmployeeAddress,
    hr_models.EmployeeRelation,
    hr_models.WorkingCalendar,
    hr_models.WorkPattern,
    hr_models.ScheduleExchange,
    hr_models.ContractType,
    hr_models.ContractAllowance,
    hr_models.ContractOvertimeComponent,
    hr_models.ContractLetterTemplate,
    hr_models.ContractLetter,
    hr_models.ContractAttachment,
    hr_models.DisciplinaryStage,
    hr_models.AttendanceChangeRequest,
    hr_models.LiveTrackingSession,
    hr_models.AttendanceRecompute,
    hr_models.LeaveStructure,
    hr_models.PayrollVariable,
    hr_models.SalaryPackage,
    hr_models.PayrollReportTemplate,
    hr_models.EmployeeLoanRequest,
    hr_models.LoanInstallment,
    hr_models.LoanRepayment,
    hr_models.LoanEligibilityAssignment,
    hr_models.TrainingRequest,
    hr_models.TrainingProgress,
    hr_models.AdvanceCycle,
    hr_models.ExpenseCycle,
    hr_models.AdvanceLimitConsumption,
    hr_models.ExpenseLimitConsumption,
    hr_models.EmployeeIDCardTemplate,
    hr_models.EmployeeIDCard,
    hr_models.MultipleJobAssignment,
    hr_models.OrganizationConfiguration,
    hr_models.HRAnnouncement,
    hr_models.ContractAllowanceHistory,
    hr_models.EmployeeDocumentBulkUpload,
    hr_models.AttendanceRecomputeTransaction,
    hr_models.LeaveBulkOperation,
    hr_models.EmployeeLoanOffer,
    hr_models.CareerTransitionChangeHistory,
    hr_models.TrainingCategory,
    hr_models.FaceRecognitionProfile,
    hr_models.ExpenseProductCategory,
    hr_models.HRWorkflowAudit,
]:
    admin.site.register(model)


@admin.register(hr_models.EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):
    form = EmployeeDocumentForm

    def _is_hr_reviewer(self, request):
        return request.user.is_superuser or request.user.groups.filter(name__in=FULL_HR_GROUPS).exists()

    def has_add_permission(self, request):
        return self._is_hr_reviewer(request) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return (
            self._is_hr_reviewer(request)
            and (obj is None or obj.status == hr_models.EmployeeDocument.Status.DRAFT)
            and super().has_change_permission(request, obj)
        )

    def get_readonly_fields(self, request, obj=None):
        return ("status", "verified", "version", "parent_document", "replaced_by")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(
    hr_models.CashAdvance,
    hr_models.CashAdvanceLine,
    hr_models.EmployeeDocumentHistory,
    hr_models.EmployeeOnboarding,
    hr_models.EmployeeOffboarding,
    hr_models.EmployeeLifecycleTask,
    hr_models.LeaveRequest,
    hr_models.LeaveAllocation,
    hr_models.LeaveMovement,
    hr_models.LeaveBucket,
    hr_models.LeaveBucketConsumption,
    hr_models.LeaveEncashment,
    hr_models.LeaveEncashmentBucket,
    hr_models.LeaveOpeningBalance,
    hr_models.PayrollRunChecklistItem,
    hr_models.PayrollRunPreviewLine,
    hr_models.PayrollDisbursement,
    hr_models.PayrollDisbursementLine,
    hr_models.PayrollAccountingEvent,
)
class ReadOnlyLeaveLedgerAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(hr_models.Payslip)
class PayslipAdmin(admin.ModelAdmin):
    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.payroll_run_id:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(hr_models.PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
