from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import Company, TimeStampedModel


class School(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="schools")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=180)
    address = models.TextField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_school_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Student(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        GRADUATED = "graduated", "Graduated"
        INACTIVE = "inactive", "Inactive"

    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="students")
    student_number = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    email = models.EmailField(blank=True)
    date_of_birth = models.DateField(blank=True, null=True)
    guardian_name = models.CharField(max_length=160, blank=True)
    guardian_phone = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["school", "student_number"], name="unique_student_number_per_school")]

    def __str__(self):
        return f"{self.student_number} - {self.name}"


class Course(TimeStampedModel):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="courses")
    code = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    instructor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="courses_taught")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]
        constraints = [models.UniqueConstraint(fields=["school", "code"], name="unique_course_code_per_school")]

    def __str__(self):
        return f"{self.code} - {self.title}"


class Lesson(TimeStampedModel):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField(max_length=180)
    sequence = models.PositiveIntegerField(default=10)
    content = models.TextField(blank=True)
    published = models.BooleanField(default=False)

    class Meta:
        ordering = ["course", "sequence", "id"]

    def __str__(self):
        return f"{self.course.code} · {self.title}"


class Enrollment(TimeStampedModel):
    class Status(models.TextChoices):
        ENROLLED = "enrolled", "Enrolled"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        DROPPED = "dropped", "Dropped"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="enrollments")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="enrollments")
    enrollment_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ENROLLED)
    progress = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])

    class Meta:
        ordering = ["-enrollment_date"]
        constraints = [models.UniqueConstraint(fields=["student", "course"], name="unique_student_course_enrollment")]

    def __str__(self):
        return f"{self.student} · {self.course.code}"


class LibraryBook(TimeStampedModel):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name="library_books")
    isbn = models.CharField(max_length=32, blank=True)
    title = models.CharField(max_length=200)
    author = models.CharField(max_length=160, blank=True)
    category = models.CharField(max_length=100, blank=True)
    copies_total = models.PositiveIntegerField(default=1)
    copies_available = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class BookLoan(TimeStampedModel):
    class Status(models.TextChoices):
        BORROWED = "borrowed", "Borrowed"
        RETURNED = "returned", "Returned"
        OVERDUE = "overdue", "Overdue"
        LOST = "lost", "Lost"

    book = models.ForeignKey(LibraryBook, on_delete=models.PROTECT, related_name="loans")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="book_loans")
    borrowed_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    returned_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.BORROWED)

    class Meta:
        ordering = ["status", "due_date"]

    def __str__(self):
        return f"{self.book.title} · {self.student.name}"

