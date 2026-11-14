import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0056_purchase_rfq_mail_delivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="awardstrategy",
            name="capacity_fill_limit_percent",
            field=models.PositiveSmallIntegerField(
                default=100,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name="awardstrategy",
            name="split_rank_1_ratio",
            field=models.PositiveSmallIntegerField(
                default=60,
                validators=[django.core.validators.MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="awardstrategy",
            name="split_rank_2_ratio",
            field=models.PositiveSmallIntegerField(
                default=30,
                validators=[django.core.validators.MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="awardstrategy",
            name="split_rank_3_ratio",
            field=models.PositiveSmallIntegerField(
                default=10,
                validators=[django.core.validators.MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="evaluation_compliance_weight",
            field=models.PositiveSmallIntegerField(
                default=15,
                validators=[django.core.validators.MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="evaluation_delivery_weight",
            field=models.PositiveSmallIntegerField(
                default=20,
                validators=[django.core.validators.MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="evaluation_history_weight",
            field=models.PositiveSmallIntegerField(
                default=10,
                validators=[django.core.validators.MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="evaluation_price_weight",
            field=models.PositiveSmallIntegerField(
                default=50,
                validators=[django.core.validators.MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="evaluation_term_weight",
            field=models.PositiveSmallIntegerField(
                default=5,
                validators=[django.core.validators.MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="tenderbid",
            name="compliance_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbid",
            name="delivery_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbid",
            name="history_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbid",
            name="price_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbid",
            name="rank",
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="tenderbid",
            name="scored_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="tenderbid",
            name="term_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbid",
            name="total_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbidline",
            name="compliance_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbidline",
            name="delivery_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbidline",
            name="history_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbidline",
            name="price_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbidline",
            name="rank",
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="tenderbidline",
            name="scored_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="tenderbidline",
            name="term_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="tenderbidline",
            name="total_score",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
    ]
