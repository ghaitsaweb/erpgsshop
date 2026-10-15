from django.contrib import admin

from .models import BookLoan, Course, Enrollment, Lesson, LibraryBook, School, Student


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 1


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "school", "instructor", "active")
    inlines = [LessonInline]


for model in [School, Student, Enrollment, LibraryBook, BookLoan]:
    admin.site.register(model)

