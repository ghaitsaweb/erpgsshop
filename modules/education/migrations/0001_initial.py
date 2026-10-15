import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Course',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40)),
                ('title', models.CharField(max_length=180)),
                ('description', models.TextField(blank=True)),
                ('active', models.BooleanField(default=True)),
                ('instructor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='courses_taught', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['title'],
            },
        ),
        migrations.CreateModel(
            name='Lesson',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=180)),
                ('sequence', models.PositiveIntegerField(default=10)),
                ('content', models.TextField(blank=True)),
                ('published', models.BooleanField(default=False)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lessons', to='gsshop_education.course')),
            ],
            options={
                'ordering': ['course', 'sequence', 'id'],
            },
        ),
        migrations.CreateModel(
            name='School',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=180)),
                ('address', models.TextField(blank=True)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('phone', models.CharField(blank=True, max_length=40)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schools', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='LibraryBook',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('isbn', models.CharField(blank=True, max_length=32)),
                ('title', models.CharField(max_length=200)),
                ('author', models.CharField(blank=True, max_length=160)),
                ('category', models.CharField(blank=True, max_length=100)),
                ('copies_total', models.PositiveIntegerField(default=1)),
                ('copies_available', models.PositiveIntegerField(default=1)),
                ('school', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='library_books', to='gsshop_education.school')),
            ],
            options={
                'ordering': ['title'],
            },
        ),
        migrations.AddField(
            model_name='course',
            name='school',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='courses', to='gsshop_education.school'),
        ),
        migrations.CreateModel(
            name='Student',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('student_number', models.CharField(max_length=40)),
                ('name', models.CharField(max_length=160)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('date_of_birth', models.DateField(blank=True, null=True)),
                ('guardian_name', models.CharField(blank=True, max_length=160)),
                ('guardian_phone', models.CharField(blank=True, max_length=40)),
                ('status', models.CharField(choices=[('active', 'Active'), ('graduated', 'Graduated'), ('inactive', 'Inactive')], default='active', max_length=16)),
                ('school', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='students', to='gsshop_education.school')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Enrollment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('enrollment_date', models.DateField(default=django.utils.timezone.localdate)),
                ('status', models.CharField(choices=[('enrolled', 'Enrolled'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('dropped', 'Dropped')], default='enrolled', max_length=16)),
                ('progress', models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='enrollments', to='gsshop_education.course')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='enrollments', to='gsshop_education.student')),
            ],
            options={
                'ordering': ['-enrollment_date'],
            },
        ),
        migrations.CreateModel(
            name='BookLoan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('borrowed_date', models.DateField(default=django.utils.timezone.localdate)),
                ('due_date', models.DateField()),
                ('returned_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(choices=[('borrowed', 'Borrowed'), ('returned', 'Returned'), ('overdue', 'Overdue'), ('lost', 'Lost')], default='borrowed', max_length=16)),
                ('book', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='loans', to='gsshop_education.librarybook')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='book_loans', to='gsshop_education.student')),
            ],
            options={
                'ordering': ['status', 'due_date'],
            },
        ),
        migrations.AddConstraint(
            model_name='school',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_school_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='course',
            constraint=models.UniqueConstraint(fields=('school', 'code'), name='unique_course_code_per_school'),
        ),
        migrations.AddConstraint(
            model_name='student',
            constraint=models.UniqueConstraint(fields=('school', 'student_number'), name='unique_student_number_per_school'),
        ),
        migrations.AddConstraint(
            model_name='enrollment',
            constraint=models.UniqueConstraint(fields=('student', 'course'), name='unique_student_course_enrollment'),
        ),
    ]
