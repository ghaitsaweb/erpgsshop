"""Extended Retail and Food & Beverage domain models.

The catalog mirrors the operational boundaries of HMX's POS ecosystem while
using the native GSSHOP Django data model.  Header/detail records are explicit
so every configurable line remains auditable and editable in the ERP UI.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import Company, Contact, Employee, Invoice, Product, TimeStampedModel, Warehouse


MONEY = {"max_digits": 18, "decimal_places": 2, "default": 0}
QTY = {"max_digits": 18, "decimal_places": 3, "default": 0}


class POSProfile(TimeStampedModel):
    class ProfileType(models.TextChoices):
        RETAIL = "pos_retail", "Retail"
        FNB = "pos_fnb", "Food & Beverage"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_profiles")
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    profile_type = models.CharField(max_length=20, choices=ProfileType.choices, default=ProfileType.RETAIL)
    branch = models.CharField(max_length=120, blank=True)
    currency = models.CharField(max_length=3, default="IDR")
    default_language = models.CharField(max_length=2, choices=(("id", "Indonesian"), ("en", "English")), default="en")
    pic = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="managed_pos_profiles")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="pos_profiles")
    active = models.BooleanField(default=True)
    multi_session = models.BooleanField(default=False)
    allow_cashbox = models.BooleanField(default=True)
    show_expected_closing_balance = models.BooleanField(default=True)
    allow_stock_check = models.BooleanField(default=True)
    allow_oversell = models.BooleanField(default=False)
    allow_preorder = models.BooleanField(default=False)
    allow_home_delivery = models.BooleanField(default=False)
    allow_loyalty = models.BooleanField(default=True)
    allow_void = models.BooleanField(default=True)
    allow_return = models.BooleanField(default=True)
    allow_exchange = models.BooleanField(default=True)
    allow_global_discount = models.BooleanField(default=True)
    allow_line_discount = models.BooleanField(default=True)
    allow_change_price = models.BooleanField(default=False)
    allow_change_quantity = models.BooleanField(default=True)
    auto_print_receipt = models.BooleanField(default=False)
    use_area_table = models.BooleanField(default=False)
    use_reservation = models.BooleanField(default=False)
    allow_service_charge = models.BooleanField(default=False)
    allow_takeaway_fee = models.BooleanField(default=False)
    validate_product_availability = models.BooleanField(default=False)
    capture_guest_information = models.BooleanField(default=False)
    allow_customer_queue = models.BooleanField(default=False)
    queue_prefix = models.CharField(max_length=10, blank=True)
    queue_number_length = models.PositiveSmallIntegerField(default=3)
    next_queue_number = models.PositiveIntegerField(default=1)
    queue_reset_method = models.CharField(max_length=16, choices=(("daily", "Daily"), ("manual", "Manual"), ("maximum", "At Maximum")), default="daily")
    internal_consumption_enabled = models.BooleanField(default=False)
    allow_wastage = models.BooleanField(default=False)
    allow_complimentary = models.BooleanField(default=False)
    allow_employee_meal = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_pos_profile_code")]

    def clean(self):
        if self.warehouse_id and self.warehouse.company_id != self.company_id:
            raise ValidationError({"warehouse": "Warehouse must belong to the POS profile company."})
        if not 1 <= self.queue_number_length <= 8:
            raise ValidationError({"queue_number_length": "Queue number length must be between 1 and 8."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class POSProductCategory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_product_categories")
    parent = models.ForeignKey("self", on_delete=models.PROTECT, blank=True, null=True, related_name="children")
    name = models.CharField(max_length=120)
    sequence = models.PositiveIntegerField(default=10)
    color = models.CharField(max_length=16, default="#0F766E")
    active = models.BooleanField(default=True)
    products = models.ManyToManyField(Product, blank=True, related_name="pos_categories")

    class Meta:
        ordering = ["sequence", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_pos_category_name")]

    def __str__(self):
        return self.name


class POSProductAttribute(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_product_attributes")
    name = models.CharField(max_length=120)
    display_type = models.CharField(max_length=16, choices=(("radio", "Radio"), ("select", "Select"), ("color", "Color"), ("button", "Button")), default="select")
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_pos_product_attribute")]

    def __str__(self):
        return self.name


class POSProductAttributeValue(TimeStampedModel):
    attribute = models.ForeignKey(POSProductAttribute, on_delete=models.CASCADE, related_name="values")
    name = models.CharField(max_length=120)
    color_code = models.CharField(max_length=16, blank=True)
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["attribute", "sequence", "name"]
        constraints = [models.UniqueConstraint(fields=["attribute", "name"], name="unique_pos_attribute_value")]

    def __str__(self):
        return f"{self.attribute} · {self.name}"


class POSProductVariant(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="pos_variants")
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=160)
    attribute_values = models.ManyToManyField(POSProductAttributeValue, blank=True, related_name="variants")
    pos_category = models.ForeignKey(POSProductCategory, on_delete=models.SET_NULL, blank=True, null=True, related_name="variants")
    barcode = models.CharField(max_length=120, blank=True)
    sale_price = models.DecimalField(**MONEY)
    estimated_cost = models.DecimalField(**MONEY)
    cross_selling = models.BooleanField(default=False)
    cross_selling_products = models.ManyToManyField(Product, blank=True, related_name="cross_sold_by_variants")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["product", "name"]
        constraints = [models.UniqueConstraint(fields=["product", "code"], name="unique_pos_product_variant")]

    def __str__(self):
        return f"{self.product} · {self.name}"


class POSProductBundle(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_product_bundles")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    sale_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="sale_bundles")
    bundle_price = models.DecimalField(**MONEY)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_pos_product_bundle")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class POSProductBundleLine(TimeStampedModel):
    bundle = models.ForeignKey(POSProductBundle, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_bundle_lines")
    variant = models.ForeignKey(POSProductVariant, on_delete=models.SET_NULL, blank=True, null=True, related_name="bundle_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["bundle", "sequence", "id"]

    def __str__(self):
        return f"{self.bundle} · {self.product}"


class POSStation(TimeStampedModel):
    class StationType(models.TextChoices):
        CASHIER = "cashier", "Cashier"
        SALES = "sales_waiters", "Waiter / Salesperson"
        KITCHEN = "kitchen", "Kitchen"
        PRICE_CHECKER = "price_checker", "Price Checker"

    profile = models.ForeignKey(POSProfile, on_delete=models.CASCADE, related_name="stations")
    name = models.CharField(max_length=255)
    station_type = models.CharField(max_length=32, choices=StationType.choices)
    authorized_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="pos_stations")
    product_categories = models.ManyToManyField(POSProductCategory, blank=True, related_name="stations")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["profile", "name"]
        constraints = [models.UniqueConstraint(fields=["profile", "name"], name="unique_pos_station_name")]

    def clean(self):
        if self.station_type == self.StationType.PRICE_CHECKER and self.pk and self.product_categories.exists():
            raise ValidationError({"product_categories": "Price checker stations cannot be category-scoped."})

    def __str__(self):
        return f"{self.profile.code} · {self.name}"


class POSDevice(TimeStampedModel):
    profile = models.ForeignKey(POSProfile, on_delete=models.CASCADE, related_name="devices")
    station = models.ForeignKey(POSStation, on_delete=models.SET_NULL, blank=True, null=True, related_name="devices")
    name = models.CharField(max_length=120)
    device_identifier = models.CharField(max_length=128)
    branch = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["profile", "name"]
        constraints = [models.UniqueConstraint(fields=["profile", "device_identifier"], name="unique_pos_device_identifier")]

    def __str__(self):
        return f"{self.profile.code} · {self.name}"


class POSDeviceSequenceState(TimeStampedModel):
    device = models.OneToOneField(POSDevice, on_delete=models.CASCADE, related_name="sequence_state")
    last_running_number = models.PositiveBigIntegerField(default=0)
    generated_at = models.DateTimeField(blank=True, null=True)
    last_synced_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.device} · {self.last_running_number}"


class POSPricelist(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_pricelists")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    currency = models.CharField(max_length=3, default="IDR")
    valid_from = models.DateField(blank=True, null=True)
    valid_until = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=True)
    profiles = models.ManyToManyField(POSProfile, blank=True, related_name="pricelists")

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_pos_pricelist_code")]

    def clean(self):
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValidationError({"valid_until": "End date must be on or after start date."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class POSPricelistRule(TimeStampedModel):
    pricelist = models.ForeignKey(POSPricelist, on_delete=models.CASCADE, related_name="rules")
    sequence = models.PositiveIntegerField(default=10)
    apply_on = models.CharField(max_length=24, choices=(("all", "All Products"), ("pos_category", "POS Category"), ("product", "Product")), default="all")
    pos_category = models.ForeignKey(POSProductCategory, on_delete=models.PROTECT, blank=True, null=True, related_name="pricelist_rules")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_pricelist_rules")
    minimum_quantity = models.DecimalField(**QTY)
    price_method = models.CharField(max_length=16, choices=(("fixed", "Fixed Price"), ("discount", "Discount"), ("markup", "Markup")), default="fixed")
    value = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["pricelist", "sequence", "id"]

    def clean(self):
        if self.apply_on == "pos_category" and not self.pos_category_id:
            raise ValidationError({"pos_category": "POS category is required for this rule."})
        if self.apply_on == "product" and not self.product_id:
            raise ValidationError({"product": "Product is required for this rule."})

    def __str__(self):
        return f"{self.pricelist.code} · rule {self.sequence}"


class POSPromotion(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_promotions")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    usage = models.CharField(max_length=16, choices=(("automatic", "Automatic"), ("manual", "Manual"), ("coupon", "Coupon")), default="automatic")
    criteria_type = models.CharField(max_length=16, choices=(("all", "All"), ("any", "Any")), default="all")
    start_at = models.DateTimeField(blank=True, null=True)
    end_at = models.DateTimeField(blank=True, null=True)
    birthday_promotion = models.BooleanField(default=False)
    registered_member_required = models.BooleanField(default=False)
    tebus_murah = models.BooleanField(default=False)
    profiles = models.ManyToManyField(POSProfile, blank=True, related_name="promotions")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_pos_promotion_code")]

    def clean(self):
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValidationError({"end_at": "Promotion end must be after its start."})
        if (self.birthday_promotion or self.tebus_murah) and self.usage != "manual":
            raise ValidationError({"usage": "Birthday and Tebus Murah promotions must use Manual usage."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class POSPromotionCriterion(TimeStampedModel):
    promotion = models.ForeignKey(POSPromotion, on_delete=models.CASCADE, related_name="criteria")
    sequence = models.PositiveIntegerField(default=10)
    criterion_type = models.CharField(max_length=24, choices=(("minimum_amount", "Minimum Amount"), ("minimum_quantity", "Minimum Quantity"), ("product", "Product"), ("pos_category", "POS Category"), ("payment_method", "Payment Method")), default="minimum_amount")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_promotion_criteria")
    pos_category = models.ForeignKey(POSProductCategory, on_delete=models.PROTECT, blank=True, null=True, related_name="promotion_criteria")
    threshold = models.DecimalField(**MONEY)
    notes = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["promotion", "sequence", "id"]

    def __str__(self):
        return f"{self.promotion.code} · {self.get_criterion_type_display()}"


class POSPromotionRule(TimeStampedModel):
    promotion = models.ForeignKey(POSPromotion, on_delete=models.CASCADE, related_name="rules")
    name = models.CharField(max_length=160)
    sequence = models.PositiveIntegerField(default=10)
    criteria_type = models.CharField(max_length=16, choices=(("all", "All"), ("any", "Any")), default="all")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["promotion", "sequence", "id"]

    def __str__(self):
        return f"{self.promotion.code} · {self.name}"


class POSPromotionReward(TimeStampedModel):
    rule = models.ForeignKey(POSPromotionRule, on_delete=models.CASCADE, related_name="rewards")
    reward_type = models.CharField(max_length=20, choices=(("discount", "Discount"), ("free_product", "Free Product"), ("add_on", "Add-on Deal"), ("loyalty", "Loyalty Point")), default="discount")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_promotion_rewards")
    value = models.DecimalField(**MONEY)
    maximum_amount = models.DecimalField(**MONEY)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)

    def clean(self):
        if self.reward_type in {"free_product", "add_on"} and not self.product_id:
            raise ValidationError({"product": "A product is required for this reward type."})

    def __str__(self):
        return f"{self.rule} · {self.get_reward_type_display()}"


class POSPromotionRewardAddonLine(TimeStampedModel):
    reward = models.ForeignKey(POSPromotionReward, on_delete=models.CASCADE, related_name="add_on_lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_add_on_rewards")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    add_on_price = models.DecimalField(**MONEY)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["reward", "sequence", "id"]

    def __str__(self):
        return f"{self.reward} · {self.product}"


class CustomerFacingCampaign(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_campaigns")
    name = models.CharField(max_length=255)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    default_slide_duration = models.PositiveIntegerField(default=7)
    scope = models.CharField(max_length=20, choices=(("all", "All POS Profiles"), ("branch", "By Branch"), ("profile", "Selected Profiles")), default="all")
    branches = models.JSONField(default=list, blank=True)
    profiles = models.ManyToManyField(POSProfile, blank=True, related_name="campaigns")
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["sequence", "id"]

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Campaign end date must follow its start date."})

    def __str__(self):
        return self.name


class CustomerFacingCampaignSlide(TimeStampedModel):
    campaign = models.ForeignKey(CustomerFacingCampaign, on_delete=models.CASCADE, related_name="slides")
    title = models.CharField(max_length=160)
    media_type = models.CharField(max_length=12, choices=(("image", "Image"), ("video", "Video")), default="image")
    media_url = models.URLField(max_length=500)
    duration_seconds = models.PositiveIntegerField(default=7)
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["campaign", "sequence", "id"]

    def __str__(self):
        return f"{self.campaign} · {self.title}"


class CustomerFacingCampaignRunningText(TimeStampedModel):
    campaign = models.ForeignKey(CustomerFacingCampaign, on_delete=models.CASCADE, related_name="running_texts")
    text = models.CharField(max_length=500)
    speed = models.PositiveIntegerField(default=5)
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["campaign", "sequence", "id"]

    def __str__(self):
        return f"{self.campaign} · {self.text[:40]}"


class POSBOM(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_boms")
    name = models.CharField(max_length=160)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_boms")
    pos_category = models.ForeignKey(POSProductCategory, on_delete=models.PROTECT, blank=True, null=True, related_name="boms")
    output_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    estimated_cost = models.DecimalField(**MONEY)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "product"], name="unique_pos_bom_product")]

    def __str__(self):
        return self.name


class POSBOMLine(TimeStampedModel):
    bom = models.ForeignKey(POSBOM, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_bom_component_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit = models.CharField(max_length=24, default="Unit")
    removable = models.BooleanField(default=False)
    nested_bom = models.ForeignKey(POSBOM, on_delete=models.PROTECT, blank=True, null=True, related_name="parent_lines")

    class Meta:
        ordering = ["bom", "id"]

    def __str__(self):
        return f"{self.bom} · {self.product}"


class POSBOMOption(TimeStampedModel):
    bom = models.ForeignKey(POSBOM, on_delete=models.CASCADE, related_name="options")
    name = models.CharField(max_length=120)
    maximum_pick = models.PositiveIntegerField(default=1)
    required = models.BooleanField(default=False)
    based_on = models.CharField(max_length=16, choices=(("product", "Product"), ("variant", "Variant")), default="product")
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.bom} · {self.name}"


class POSBOMOptionLine(TimeStampedModel):
    option = models.ForeignKey(POSBOMOption, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_bom_option_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    extra_price = models.DecimalField(**MONEY)
    required = models.BooleanField(default=False)
    default = models.BooleanField(default=False)
    item_note = models.CharField(max_length=240, blank=True)

    def __str__(self):
        return f"{self.option} · {self.product}"


class POSPaymentMethod(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_payment_methods")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    method_type = models.CharField(max_length=16, choices=(("cash", "Cash"), ("bank", "Bank"), ("card", "Card"), ("deposit", "Member Deposit"), ("other", "Other")), default="cash")
    integration_type = models.CharField(max_length=32, blank=True)
    sequence = models.PositiveIntegerField(default=10)
    requires_approval = models.BooleanField(default=False)
    use_mdr = models.BooleanField(default=False)
    deposit_payment = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_pos_payment_method")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class POSProfilePaymentMethodLine(TimeStampedModel):
    profile = models.ForeignKey(POSProfile, on_delete=models.CASCADE, related_name="payment_method_lines")
    payment_method = models.ForeignKey(POSPaymentMethod, on_delete=models.PROTECT, related_name="profile_lines")
    selected = models.BooleanField(default=True)
    default_cash_amount = models.DecimalField(**MONEY)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["profile", "sequence", "id"]
        constraints = [models.UniqueConstraint(fields=["profile", "payment_method"], name="unique_profile_payment_method")]

    def __str__(self):
        return f"{self.profile} · {self.payment_method}"


class POSPriceShortcutPreset(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_price_shortcuts")
    name = models.CharField(max_length=120)
    currency = models.CharField(max_length=3, default="IDR")
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class POSPriceShortcutLine(TimeStampedModel):
    preset = models.ForeignKey(POSPriceShortcutPreset, on_delete=models.CASCADE, related_name="lines")
    label = models.CharField(max_length=60)
    value = models.DecimalField(**MONEY)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["preset", "sequence", "id"]

    def __str__(self):
        return f"{self.preset} · {self.label}"


class POSHomeDeliveryFee(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_home_delivery_fees")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    calculation_type = models.CharField(max_length=16, choices=(("fixed", "Fixed"), ("percentage", "Percentage"), ("distance", "By Distance")), default="fixed")
    tax_calculation = models.CharField(max_length=16, choices=(("included", "Tax Included"), ("excluded", "Tax Excluded"), ("none", "No Tax")), default="none")
    amount = models.DecimalField(**MONEY)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_pos_delivery_fee")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class POSReceiptTemplate(TimeStampedModel):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_receipt_templates")
    name = models.CharField(max_length=160)
    template_type = models.CharField(max_length=20, choices=(("bill", "Bill"), ("checker", "Checker"), ("label", "Label"), ("kitchen", "Kitchen")), default="bill")
    paper_size = models.CharField(max_length=20, default="80mm")
    label_size = models.CharField(max_length=20, blank=True)
    language = models.CharField(max_length=2, choices=(("id", "Indonesian"), ("en", "English")), default="en")
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    branch = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    ocr_review_required = models.BooleanField(default=False)
    builder_normalized = models.BooleanField(default=False)
    stamp_image_urls = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class POSReceiptSection(TimeStampedModel):
    template = models.ForeignKey(POSReceiptTemplate, on_delete=models.CASCADE, related_name="sections")
    key = models.CharField(max_length=64)
    name = models.CharField(max_length=120)
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)
    mandatory = models.BooleanField(default=False)
    alignment = models.CharField(max_length=12, choices=(("left", "Left"), ("center", "Center"), ("right", "Right")), default="left")
    font_size = models.CharField(max_length=12, default="normal")
    bold = models.BooleanField(default=False)

    class Meta:
        ordering = ["template", "sequence", "id"]
        constraints = [models.UniqueConstraint(fields=["template", "key"], name="unique_receipt_section_key")]

    def __str__(self):
        return f"{self.template} · {self.name}"


class POSReceiptField(TimeStampedModel):
    section = models.ForeignKey(POSReceiptSection, on_delete=models.CASCADE, related_name="fields")
    key = models.CharField(max_length=64)
    label = models.CharField(max_length=120)
    block_type = models.CharField(max_length=20, choices=(("text", "Text"), ("image", "Image"), ("line", "Line"), ("table", "Table"), ("spacing", "Spacing")), default="text")
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)
    mandatory = models.BooleanField(default=False)
    data_source = models.CharField(max_length=120, blank=True)
    custom_value = models.TextField(blank=True)
    print_config = models.JSONField(default=dict, blank=True)
    spacing = models.PositiveIntegerField(default=0)
    alignment = models.CharField(max_length=12, choices=(("left", "Left"), ("center", "Center"), ("right", "Right")), default="left")
    font_size = models.CharField(max_length=12, default="normal")
    bold = models.BooleanField(default=False)
    ocr_detected = models.BooleanField(default=False)
    ocr_needs_review = models.BooleanField(default=False)

    class Meta:
        ordering = ["section", "sequence", "id"]
        constraints = [models.UniqueConstraint(fields=["section", "key"], name="unique_receipt_field_key")]

    def __str__(self):
        return f"{self.section} · {self.label}"


class POSReceiptTemplateRevision(TimeStampedModel):
    template = models.ForeignKey(POSReceiptTemplate, on_delete=models.CASCADE, related_name="revisions")
    revision_number = models.PositiveIntegerField()
    snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="pos_receipt_revisions")

    class Meta:
        ordering = ["template", "-revision_number"]
        constraints = [models.UniqueConstraint(fields=["template", "revision_number"], name="unique_receipt_revision")]

    def __str__(self):
        return f"{self.template} · R{self.revision_number}"


class POSEmailTemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_email_templates")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    language = models.CharField(max_length=2, choices=(("id", "Indonesian"), ("en", "English")), default="en")
    subject = models.CharField(max_length=255)
    body_html = models.TextField()
    template_type = models.CharField(max_length=24, choices=(("receipt", "Receipt"), ("otp", "Approval OTP"), ("membership", "Membership")), default="receipt")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_pos_email_template")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class POSReceiptOCRJob(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        REVIEW = "review", "Needs Review"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    template = models.ForeignKey(POSReceiptTemplate, on_delete=models.CASCADE, related_name="ocr_jobs")
    source_file = models.FileField(upload_to="pos/receipt-ocr/")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    extracted_layout = models.JSONField(default=dict, blank=True)
    confidence = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_receipt_ocr_jobs")
    processed_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template} · OCR #{self.pk or 'new'}"


class POSBackupData(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    name = models.CharField(max_length=160)
    profiles = models.ManyToManyField(POSProfile, blank=True, related_name="backup_jobs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    backup_files = models.JSONField(default=list, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_backup_jobs")
    completed_at = models.DateTimeField(blank=True, null=True)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class POSBackupDataProfile(TimeStampedModel):
    backup = models.ForeignKey(POSBackupData, on_delete=models.CASCADE, related_name="profile_files")
    profile = models.ForeignKey(POSProfile, on_delete=models.PROTECT, related_name="backup_files")
    file = models.FileField(upload_to="pos/backups/", blank=True)
    status = models.CharField(max_length=16, choices=POSBackupData.Status.choices, default=POSBackupData.Status.DRAFT)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["backup", "profile"]
        constraints = [models.UniqueConstraint(fields=["backup", "profile"], name="unique_pos_backup_profile")]

    def __str__(self):
        return f"{self.backup} · {self.profile}"


class POSBackupDataLine(TimeStampedModel):
    backup = models.ForeignKey(POSBackupData, on_delete=models.CASCADE, related_name="progress_lines")
    profile = models.ForeignKey(POSProfile, on_delete=models.PROTECT, related_name="backup_progress_lines")
    model_name = models.CharField(max_length=120)
    progress = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    state = models.CharField(max_length=16, choices=(("pending", "Pending"), ("running", "Running"), ("done", "Done"), ("failed", "Failed")), default="pending")
    description = models.CharField(max_length=240, blank=True)
    message = models.TextField(blank=True)
    step = models.PositiveIntegerField(default=0)
    step_max = models.PositiveIntegerField(default=0)
    domain = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["backup", "profile", "step", "id"]

    def __str__(self):
        return f"{self.backup} · {self.model_name}"


class POSPayment(TimeStampedModel):
    order = models.ForeignKey("POSOrder", on_delete=models.CASCADE, related_name="payments")
    payment_method = models.ForeignKey(POSPaymentMethod, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(**MONEY)
    reference = models.CharField(max_length=120, blank=True)
    approval_code = models.CharField(max_length=120, blank=True)
    payment_date = models.DateTimeField(default=timezone.now)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_payments")
    card_bin = models.CharField(max_length=12, blank=True)
    local_uuid = models.CharField(max_length=64, blank=True, null=True, unique=True)
    mdr_percentage = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    mdr_charge_amount = models.DecimalField(**MONEY)
    deposit_balance_before = models.DecimalField(**MONEY)
    deposit_balance_after = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["-payment_date", "-id"]

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({"amount": "Payment amount must be greater than zero."})

    def __str__(self):
        return f"{self.order} · {self.payment_method} · {self.amount}"


class POSCashMovement(TimeStampedModel):
    class MovementType(models.TextChoices):
        CASH_IN = "cash_in", "Cash In"
        CASH_OUT = "cash_out", "Cash Out"

    session = models.ForeignKey("POSSession", on_delete=models.PROTECT, related_name="cash_movements")
    reference = models.CharField(max_length=64)
    movement_type = models.CharField(max_length=12, choices=MovementType.choices)
    amount = models.DecimalField(**MONEY)
    reason = models.CharField(max_length=160)
    notes = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_cash_movements")
    requested_at = models.DateTimeField(default=timezone.now)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["session", "reference"], name="unique_session_cash_reference")]

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({"amount": "Cash movement amount must be greater than zero."})

    def __str__(self):
        return self.reference


class POSApprovalOTPRule(TimeStampedModel):
    profile = models.ForeignKey(POSProfile, on_delete=models.CASCADE, related_name="otp_rules")
    action_type = models.CharField(max_length=32, choices=(("void", "Void"), ("return", "Return"), ("price", "Price Change"), ("quantity", "Quantity Change"), ("discount", "Discount"), ("variance", "Cash Variance")), default="void")
    approvers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="pos_otp_rules")
    expiry_seconds = models.PositiveIntegerField(default=300)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["profile", "action_type"]
        constraints = [models.UniqueConstraint(fields=["profile", "action_type"], name="unique_pos_otp_rule")]

    def __str__(self):
        return f"{self.profile} · {self.get_action_type_display()}"


class POSApprovalOTPRequest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        EXPIRED = "expired", "Expired"
        CONSUMED = "consumed", "Consumed"
        REJECTED = "rejected", "Rejected"

    request_reference = models.CharField(max_length=80, unique=True)
    target_reference = models.CharField(max_length=120, blank=True)
    rule = models.ForeignKey(POSApprovalOTPRule, on_delete=models.PROTECT, related_name="requests")
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_otp_requests")
    requested_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    otp_hash = models.CharField(max_length=255)
    context_hash = models.CharField(max_length=255, blank=True)
    approval_token_hash = models.CharField(max_length=255, blank=True)
    approval_consumed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return self.request_reference


class POSReceiptPrintLog(TimeStampedModel):
    order = models.ForeignKey("POSOrder", on_delete=models.CASCADE, related_name="receipt_print_logs")
    session = models.ForeignKey("POSSession", on_delete=models.PROTECT, related_name="receipt_print_logs")
    profile = models.ForeignKey(POSProfile, on_delete=models.PROTECT, related_name="receipt_print_logs")
    printed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_receipt_prints")
    printed_at = models.DateTimeField(default=timezone.now)
    print_count = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-printed_at"]

    def __str__(self):
        return f"{self.order} · print {self.print_count}"


class POSReceiptEmailDelivery(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    order = models.ForeignKey("POSOrder", on_delete=models.CASCADE, related_name="receipt_emails")
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_receipt_emails")
    requested_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(blank=True, null=True)
    attempt_number = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    failure_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.order} · {self.recipient}"


class POSPriceLabelPrintLog(TimeStampedModel):
    pricelist = models.ForeignKey(POSPricelist, on_delete=models.PROTECT, related_name="label_print_logs")
    template = models.ForeignKey(POSReceiptTemplate, on_delete=models.PROTECT, related_name="label_print_logs")
    product_selection = models.CharField(max_length=120, blank=True)
    product_count = models.PositiveIntegerField(default=0)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_label_prints")
    generated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"{self.pricelist} · {self.generated_at:%Y-%m-%d %H:%M}"


class POSPriceCheckLog(TimeStampedModel):
    station = models.ForeignKey(POSStation, on_delete=models.PROTECT, related_name="price_checks")
    profile = models.ForeignKey(POSProfile, on_delete=models.PROTECT, related_name="price_checks")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_price_checks")
    product_name = models.CharField(max_length=160)
    barcode = models.CharField(max_length=120, blank=True)
    checked_price = models.DecimalField(**MONEY)
    checked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-checked_at"]

    def __str__(self):
        return f"{self.product_name} · {self.checked_at:%Y-%m-%d %H:%M}"


class LoyaltyProgram(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="loyalty_programs")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    point_currency_rate = models.DecimalField(max_digits=18, decimal_places=6, default=1)
    minimum_redemption = models.DecimalField(**MONEY)
    valid_from = models.DateField(blank=True, null=True)
    valid_until = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_loyalty_program_code")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class LoyaltyTier(TimeStampedModel):
    program = models.ForeignKey(LoyaltyProgram, on_delete=models.CASCADE, related_name="tiers")
    name = models.CharField(max_length=120)
    minimum_points = models.DecimalField(**MONEY)
    multiplier = models.DecimalField(max_digits=7, decimal_places=4, default=1)
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["program", "minimum_points", "sequence"]

    def __str__(self):
        return f"{self.program} · {self.name}"


class LoyaltyWallet(TimeStampedModel):
    program = models.ForeignKey(LoyaltyProgram, on_delete=models.PROTECT, related_name="wallets")
    member = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="loyalty_wallets")
    tier = models.ForeignKey(LoyaltyTier, on_delete=models.SET_NULL, blank=True, null=True, related_name="wallets")
    points_balance = models.DecimalField(**MONEY)
    lifetime_points = models.DecimalField(**MONEY)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["member__name"]
        constraints = [models.UniqueConstraint(fields=["program", "member"], name="unique_member_loyalty_wallet")]

    def __str__(self):
        return f"{self.member} · {self.points_balance} pts"


class LoyaltyTransaction(TimeStampedModel):
    wallet = models.ForeignKey(LoyaltyWallet, on_delete=models.PROTECT, related_name="transactions")
    order = models.ForeignKey("POSOrder", on_delete=models.SET_NULL, blank=True, null=True, related_name="loyalty_transactions")
    transaction_type = models.CharField(max_length=16, choices=(("earn", "Earn"), ("redeem", "Redeem"), ("opening", "Opening Balance"), ("adjustment", "Adjustment"), ("expiry", "Expiry")), default="earn")
    points = models.DecimalField(**MONEY)
    balance_after = models.DecimalField(**MONEY)
    reference = models.CharField(max_length=120, blank=True)
    transaction_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-transaction_at", "-id"]

    def __str__(self):
        return f"{self.wallet} · {self.get_transaction_type_display()} {self.points}"


class LoyaltyWelcomePoint(TimeStampedModel):
    program = models.ForeignKey(LoyaltyProgram, on_delete=models.CASCADE, related_name="welcome_points")
    name = models.CharField(max_length=120)
    bonus_points = models.PositiveIntegerField(default=0)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.program} · {self.name}"


class LoyaltyTierEvent(TimeStampedModel):
    wallet = models.ForeignKey(LoyaltyWallet, on_delete=models.PROTECT, related_name="tier_events")
    previous_tier = models.ForeignKey(LoyaltyTier, on_delete=models.SET_NULL, blank=True, null=True, related_name="previous_events")
    new_tier = models.ForeignKey(LoyaltyTier, on_delete=models.PROTECT, related_name="new_events")
    changed_at = models.DateTimeField(default=timezone.now)
    reason = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.wallet} · {self.new_tier}"


class POSRFMConfiguration(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name="pos_rfm_configuration")
    recency_days = models.PositiveIntegerField(default=90)
    frequency_period_days = models.PositiveIntegerField(default=365)
    monetary_period_days = models.PositiveIntegerField(default=365)
    minimum_orders = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"RFM · {self.company}"


class POSRFMCalculationRun(TimeStampedModel):
    configuration = models.ForeignKey(POSRFMConfiguration, on_delete=models.PROTECT, related_name="runs")
    run_at = models.DateTimeField(default=timezone.now)
    run_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_rfm_runs")
    customer_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=(("pending", "Pending"), ("running", "Running"), ("done", "Done"), ("failed", "Failed")), default="pending")
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-run_at"]

    def __str__(self):
        return f"{self.configuration} · {self.run_at:%Y-%m-%d %H:%M}"


class POSRFMCustomerResult(TimeStampedModel):
    run = models.ForeignKey(POSRFMCalculationRun, on_delete=models.CASCADE, related_name="results")
    customer = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="pos_rfm_results")
    recency_days = models.PositiveIntegerField(default=0)
    frequency = models.PositiveIntegerField(default=0)
    monetary_value = models.DecimalField(**MONEY)
    recency_score = models.PositiveSmallIntegerField(default=1, validators=[MaxValueValidator(5)])
    frequency_score = models.PositiveSmallIntegerField(default=1, validators=[MaxValueValidator(5)])
    monetary_score = models.PositiveSmallIntegerField(default=1, validators=[MaxValueValidator(5)])
    segment = models.CharField(max_length=80)

    class Meta:
        ordering = ["segment", "customer__name"]
        constraints = [models.UniqueConstraint(fields=["run", "customer"], name="unique_rfm_customer_run")]

    def __str__(self):
        return f"{self.customer} · {self.segment}"


class POSRFMBroadcastLog(TimeStampedModel):
    result = models.ForeignKey(POSRFMCustomerResult, on_delete=models.PROTECT, related_name="broadcast_logs")
    channel = models.CharField(max_length=16, choices=(("email", "Email"), ("whatsapp", "WhatsApp"), ("sms", "SMS")), default="email")
    destination = models.CharField(max_length=160)
    sent_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=(("sent", "Sent"), ("failed", "Failed")), default="sent")
    response = models.TextField(blank=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.result} · {self.channel}"


class POSArea(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_areas")
    name = models.CharField(max_length=64)
    sync_uuid = models.CharField(max_length=64, blank=True, null=True, unique=True)
    background_image_url = models.URLField(max_length=500, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_pos_area_name")]

    def __str__(self):
        return self.name


class POSReservation(TimeStampedModel):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        SEATED = "seated", "Seated"
        CONVERTED = "converted", "Converted to Order"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No Show"

    reference = models.CharField(max_length=64, unique=True)
    profile = models.ForeignKey(POSProfile, on_delete=models.PROTECT, related_name="reservations")
    customer = models.ForeignKey(Contact, on_delete=models.SET_NULL, blank=True, null=True, related_name="pos_reservations")
    guest_display_name = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    reservation_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField(blank=True, null=True)
    reservation_type = models.CharField(max_length=16, choices=(("dine_in", "Dine In"), ("event", "Event"), ("takeaway", "Takeaway")), default="dine_in")
    area = models.ForeignKey(POSArea, on_delete=models.PROTECT, related_name="reservations")
    tables = models.ManyToManyField("RestaurantTable", blank=True, related_name="reservations")
    primary_table = models.ForeignKey("RestaurantTable", on_delete=models.SET_NULL, blank=True, null=True, related_name="primary_reservations")
    pax = models.PositiveIntegerField(default=1)
    remarks = models.TextField(blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    local_reservation_id = models.CharField(max_length=64, blank=True)
    command_id = models.CharField(max_length=64, blank=True)
    reschedule_count = models.PositiveIntegerField(default=0)
    last_rescheduled_at = models.DateTimeField(blank=True, null=True)
    reschedule_reason = models.CharField(max_length=240, blank=True)
    converted_order = models.ForeignKey("POSOrder", on_delete=models.SET_NULL, blank=True, null=True, related_name="source_reservations")
    cancelled_at = models.DateTimeField(blank=True, null=True)
    cancel_reason = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-reservation_date", "-start_time"]

    def clean(self):
        if self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "Reservation end time must be after its start time."})
        if not self.customer_id and not self.guest_display_name.strip():
            raise ValidationError({"guest_display_name": "Select a customer or enter the guest name."})

    def __str__(self):
        return self.reference


class POSReservationLine(TimeStampedModel):
    reservation = models.ForeignKey(POSReservation, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_reservation_lines")
    description = models.CharField(max_length=200, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    notes = models.TextField(blank=True)
    planned_start = models.DateTimeField(blank=True, null=True)
    planned_end = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["reservation", "id"]

    def __str__(self):
        return f"{self.reservation} · {self.product}"


class KitchenTicketLine(TimeStampedModel):
    ticket = models.ForeignKey("KitchenTicket", on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey("POSOrderLine", on_delete=models.PROTECT, related_name="kitchen_lines")
    reference = models.CharField(max_length=64, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    notes = models.TextField(blank=True)
    unit = models.CharField(max_length=24, default="Unit")
    status = models.CharField(max_length=16, choices=(("queued", "Queued"), ("preparing", "Preparing"), ("ready", "Ready"), ("served", "Served"), ("cancelled", "Cancelled")), default="queued")
    ready_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    display_sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["ticket", "display_sequence", "id"]

    def __str__(self):
        return f"{self.ticket} · {self.order_line.product}"


class POSCombo(TimeStampedModel):
    name = models.CharField(max_length=160)
    product = models.OneToOneField(Product, on_delete=models.PROTECT, related_name="pos_combo")
    pos_category = models.ForeignKey(POSProductCategory, on_delete=models.PROTECT, blank=True, null=True, related_name="combos")
    price = models.DecimalField(**MONEY)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class POSComboCategory(TimeStampedModel):
    combo = models.ForeignKey(POSCombo, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=120)
    minimum_pick = models.PositiveIntegerField(default=0)
    maximum_pick = models.PositiveIntegerField(default=1)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["combo", "sequence", "id"]

    def clean(self):
        if self.maximum_pick < self.minimum_pick:
            raise ValidationError({"maximum_pick": "Maximum pick cannot be lower than minimum pick."})

    def __str__(self):
        return f"{self.combo} · {self.name}"


class POSComboLine(TimeStampedModel):
    category = models.ForeignKey(POSComboCategory, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_combo_lines")
    product_price = models.DecimalField(**MONEY)
    additional_price = models.DecimalField(**MONEY)
    default = models.BooleanField(default=False)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["category", "sequence", "id"]

    def __str__(self):
        return f"{self.category} · {self.product}"


class POSProductAvailability(TimeStampedModel):
    profile = models.ForeignKey(POSProfile, on_delete=models.CASCADE, related_name="product_availability")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="pos_availability")
    status = models.CharField(max_length=16, choices=(("available", "Available"), ("sold_out", "Sold Out"), ("hidden", "Hidden")), default="available")
    expires_at = models.DateTimeField(blank=True, null=True)
    reason = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["profile", "product__name"]
        constraints = [models.UniqueConstraint(fields=["profile", "product"], name="unique_pos_product_availability")]

    def __str__(self):
        return f"{self.profile} · {self.product} · {self.status}"


class POSServiceCharge(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_service_charges")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    charge_type = models.CharField(max_length=16, choices=(("fixed", "Fixed"), ("percentage", "Percentage")), default="percentage")
    calculation_base = models.CharField(max_length=16, choices=(("subtotal", "Subtotal"), ("after_discount", "After Discount")), default="after_discount")
    calculation_order = models.CharField(max_length=16, choices=(("before_tax", "Before Tax"), ("after_tax", "After Tax")), default="before_tax")
    value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_pos_service_charge")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class POSTakeawayFee(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_takeaway_fees")
    name = models.CharField(max_length=120)
    calculation_type = models.CharField(max_length=16, choices=(("fixed", "Fixed"), ("percentage", "Percentage")), default="fixed")
    tax_calculation = models.CharField(max_length=16, choices=(("included", "Tax Included"), ("excluded", "Tax Excluded"), ("none", "No Tax")), default="none")
    amount = models.DecimalField(**MONEY)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class POSCustomerQueueResetRule(TimeStampedModel):
    profile = models.OneToOneField(POSProfile, on_delete=models.CASCADE, related_name="queue_reset_rule")
    starting_queue_number = models.PositiveIntegerField(default=1)
    maximum_queue_number = models.PositiveIntegerField(default=999)
    next_queue_number = models.PositiveIntegerField(default=1)
    last_reset_at = models.DateTimeField(blank=True, null=True)

    def clean(self):
        if self.maximum_queue_number < self.starting_queue_number:
            raise ValidationError({"maximum_queue_number": "Maximum queue number must not be lower than the starting number."})

    def __str__(self):
        return f"{self.profile} · next {self.next_queue_number}"


class EmployeeMealProgram(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employee_meal_programs")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    valid_from = models.DateField()
    valid_until = models.DateField(blank=True, null=True)
    currency = models.CharField(max_length=3, default="IDR")
    profiles = models.ManyToManyField(POSProfile, blank=True, related_name="employee_meal_programs")
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_employee_meal_program")]

    def clean(self):
        if self.valid_until and self.valid_until < self.valid_from:
            raise ValidationError({"valid_until": "Validity end must be on or after the start."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class EmployeeMealProgramLine(TimeStampedModel):
    program = models.ForeignKey(EmployeeMealProgram, on_delete=models.CASCADE, related_name="lines")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    sequence = models.PositiveIntegerField(default=10)
    employee_position = models.CharField(max_length=120, blank=True)
    maximum_pick = models.PositiveIntegerField(default=1)
    weekdays = models.JSONField(default=list, blank=True)
    start_time = models.TimeField(blank=True, null=True)
    end_time = models.TimeField(blank=True, null=True)
    eligible_menu_basis = models.CharField(max_length=16, choices=(("product", "Products"), ("category", "POS Categories"), ("all", "All")), default="all")
    eligible_products = models.ManyToManyField(Product, blank=True, related_name="employee_meal_program_lines")
    eligible_categories = models.ManyToManyField(POSProductCategory, blank=True, related_name="employee_meal_program_lines")
    benefit_type = models.CharField(max_length=16, choices=(("budget", "Budget Limit"), ("free_item", "Free Items"), ("full", "Fully Covered")), default="budget")
    budget_limit = models.DecimalField(**MONEY)
    free_item_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["program", "sequence", "id"]
        constraints = [models.UniqueConstraint(fields=["program", "code"], name="unique_employee_meal_program_line")]

    def __str__(self):
        return f"{self.program} · {self.name}"


class EmployeeMealProgramRecord(TimeStampedModel):
    class State(models.TextChoices):
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    reference = models.CharField(max_length=64, unique=True)
    idempotency_key = models.CharField(max_length=128, unique=True)
    payload_hash = models.CharField(max_length=128)
    program = models.ForeignKey(EmployeeMealProgram, on_delete=models.PROTECT, related_name="records")
    program_line = models.ForeignKey(EmployeeMealProgramLine, on_delete=models.PROTECT, related_name="records")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="meal_records")
    profile = models.ForeignKey(POSProfile, on_delete=models.PROTECT, related_name="employee_meal_records")
    session = models.ForeignKey("POSSession", on_delete=models.PROTECT, related_name="employee_meal_records")
    order = models.ForeignKey("POSOrder", on_delete=models.PROTECT, related_name="employee_meal_records")
    business_date = models.DateField(default=timezone.localdate)
    redeemed_at = models.DateTimeField(default=timezone.now)
    redeemed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee_meal_redemptions")
    state = models.CharField(max_length=16, choices=State.choices, default=State.POSTED)
    company_covered_amount = models.DecimalField(**MONEY)
    employee_payable_amount = models.DecimalField(**MONEY)
    actual_meal_cost = models.DecimalField(**MONEY)
    item_snapshot = models.JSONField(default=list, blank=True)
    reversed_at = models.DateTimeField(blank=True, null=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="reversed_employee_meals")
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-redeemed_at"]

    def __str__(self):
        return self.reference


class EmployeeMealProgramRecordLine(TimeStampedModel):
    record = models.ForeignKey(EmployeeMealProgramRecord, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey("POSOrderLine", on_delete=models.PROTECT, related_name="employee_meal_lines")
    client_line_id = models.CharField(max_length=64, blank=True)
    sequence = models.PositiveIntegerField(default=10)
    product_name_snapshot = models.CharField(max_length=160)
    ordered_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    covered_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    company_covered_amount = models.DecimalField(**MONEY)
    employee_payable_amount = models.DecimalField(**MONEY)
    net_amount = models.DecimalField(**MONEY)
    coverage_status = models.CharField(max_length=16, choices=(("covered", "Covered"), ("partial", "Partially Covered"), ("excluded", "Excluded")), default="covered")

    class Meta:
        ordering = ["record", "sequence", "id"]

    def __str__(self):
        return f"{self.record} · {self.product_name_snapshot}"


class POSInternalConsumptionReason(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_consumption_reasons")
    name = models.CharField(max_length=120)
    consumption_type = models.CharField(max_length=20, choices=(("wastage", "Wastage"), ("complimentary", "Complimentary"), ("employee_meal", "Employee Meal"), ("internal", "Internal Use")), default="internal")
    description = models.TextField(blank=True)
    require_approval = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["consumption_type", "name"]

    def __str__(self):
        return self.name


class POSInternalUsage(TimeStampedModel):
    reference = models.CharField(max_length=64, unique=True)
    usage_type = models.CharField(max_length=20, choices=(("wastage", "Wastage"), ("complimentary", "Complimentary"), ("employee_meal", "Employee Meal"), ("internal", "Internal Use")), default="internal")
    reason = models.ForeignKey(POSInternalConsumptionReason, on_delete=models.PROTECT, related_name="usages")
    profile = models.ForeignKey(POSProfile, on_delete=models.PROTECT, related_name="internal_usages")
    session = models.ForeignKey("POSSession", on_delete=models.PROTECT, related_name="internal_usages")
    order = models.ForeignKey("POSOrder", on_delete=models.SET_NULL, blank=True, null=True, related_name="internal_usages")
    usage_date = models.DateField(default=timezone.localdate)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_internal_usages")
    recorded_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return self.reference


class POSInternalUsageLine(TimeStampedModel):
    usage = models.ForeignKey(POSInternalUsage, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_internal_usage_lines")
    sequence = models.PositiveIntegerField(default=10)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit = models.CharField(max_length=24, default="Unit")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["usage", "sequence", "id"]

    def __str__(self):
        return f"{self.usage} · {self.product}"


class POSCashDrawerLog(TimeStampedModel):
    profile = models.ForeignKey(POSProfile, on_delete=models.PROTECT, related_name="cash_drawer_logs")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_drawer_logs")
    printer_name = models.CharField(max_length=160, blank=True)
    trigger_type = models.CharField(max_length=24, choices=(("payment", "Payment"), ("cash_movement", "Cash Movement"), ("manual", "Manual")), default="manual")
    opened_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self):
        return f"{self.profile} · {self.opened_at:%Y-%m-%d %H:%M}"


class POSFNBSyncCommand(TimeStampedModel):
    command_id = models.CharField(max_length=64, unique=True)
    order = models.ForeignKey("POSOrder", on_delete=models.CASCADE, related_name="fnb_sync_commands")
    sync_mode = models.CharField(max_length=16, choices=(("push", "Push"), ("pull", "Pull"), ("merge", "Merge")), default="push")
    payload_hash = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=(("pending", "Pending"), ("processed", "Processed"), ("failed", "Failed")), default="pending")
    result_json = models.JSONField(default=dict, blank=True)
    error_json = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.command_id


class CardPayment(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_card_payments")
    name = models.CharField(max_length=120)
    bank_name = models.CharField(max_length=120)
    bank_account_number = models.CharField(max_length=80, blank=True)
    card_type = models.CharField(max_length=20, choices=(("credit", "Credit Card"), ("debit", "Debit Card"), ("prepaid", "Prepaid Card")), default="credit")
    bin_prefix = models.CharField(max_length=12, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["bank_name", "name"]

    def __str__(self):
        return f"{self.bank_name} · {self.name}"


class PaymentMethodMDRCharge(TimeStampedModel):
    payment_method = models.ForeignKey(POSPaymentMethod, on_delete=models.CASCADE, related_name="mdr_charges")
    card_payment = models.ForeignKey(CardPayment, on_delete=models.PROTECT, related_name="mdr_charges")
    paid_by = models.CharField(max_length=16, choices=(("company", "Company"), ("customer", "Customer"), ("shared", "Shared")), default="company")
    percentage = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    surcharge_amount = models.DecimalField(**MONEY)
    card_type = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["payment_method", "card_payment"]
        constraints = [models.UniqueConstraint(fields=["payment_method", "card_payment"], name="unique_payment_method_mdr")]

    def __str__(self):
        return f"{self.payment_method} · {self.card_payment}"


class POSBankPayment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    order = models.ForeignKey("POSOrder", on_delete=models.PROTECT, related_name="bank_payments")
    payment_method = models.ForeignKey(POSPaymentMethod, on_delete=models.PROTECT, related_name="bank_payments")
    reference = models.CharField(max_length=120)
    client_request_id = models.CharField(max_length=64, unique=True)
    amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    paid_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class POSBankPaymentRequestLog(TimeStampedModel):
    payment = models.ForeignKey(POSBankPayment, on_delete=models.CASCADE, related_name="request_logs")
    client_request_id = models.CharField(max_length=64)
    origin_from = models.CharField(max_length=80, blank=True)
    description = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=16)
    log_message = models.TextField(blank=True)

    class Meta:
        ordering = ["payment", "created_at"]

    def __str__(self):
        return f"{self.payment} · {self.status}"


class POSMemberDeposit(TimeStampedModel):
    member = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="pos_deposits")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_member_deposits")
    balance = models.DecimalField(**MONEY)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["member__name"]
        constraints = [models.UniqueConstraint(fields=["company", "member"], name="unique_pos_member_deposit")]

    def __str__(self):
        return f"{self.member} · {self.balance}"


class POSDepositMovement(TimeStampedModel):
    deposit = models.ForeignKey(POSMemberDeposit, on_delete=models.PROTECT, related_name="movements")
    order = models.ForeignKey("POSOrder", on_delete=models.SET_NULL, blank=True, null=True, related_name="deposit_movements")
    payment = models.ForeignKey(POSPayment, on_delete=models.SET_NULL, blank=True, null=True, related_name="deposit_movements")
    movement_type = models.CharField(max_length=16, choices=(("topup", "Top Up"), ("payment", "Payment"), ("refund", "Refund"), ("adjustment", "Adjustment")), default="topup")
    amount = models.DecimalField(**MONEY)
    balance_before = models.DecimalField(**MONEY)
    balance_after = models.DecimalField(**MONEY)
    reference = models.CharField(max_length=120)
    movement_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_deposit_movements")

    class Meta:
        ordering = ["-movement_at"]

    def __str__(self):
        return self.reference


class POSOutletOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        PREPARING = "preparing", "Preparing"
        DISPATCHED = "dispatched", "Dispatched"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_outlet_orders")
    profile = models.ForeignKey(POSProfile, on_delete=models.PROTECT, related_name="outlet_orders")
    central_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="central_kitchen_orders")
    reference = models.CharField(max_length=64)
    order_date = models.DateField(default=timezone.localdate)
    requested_delivery_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-order_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "reference"], name="unique_pos_outlet_order")]

    def __str__(self):
        return self.reference


class POSOutletOrderLine(TimeStampedModel):
    order = models.ForeignKey(POSOutletOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_outlet_order_lines")
    requested_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    prepared_quantity = models.DecimalField(**QTY)
    received_quantity = models.DecimalField(**QTY)
    returned_quantity = models.DecimalField(**QTY)
    unit = models.CharField(max_length=24, default="Unit")
    notes = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.order} · {self.product}"


class POSStockDemand(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        TRANSFERRED = "transferred", "Transferred"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_stock_demands")
    profile = models.ForeignKey(POSProfile, on_delete=models.PROTECT, related_name="stock_demands")
    source_order = models.ForeignKey("POSOrder", on_delete=models.SET_NULL, blank=True, null=True, related_name="stock_demands")
    reference = models.CharField(max_length=64)
    demand_date = models.DateField(default=timezone.localdate)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="pos_stock_demands")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-demand_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "reference"], name="unique_pos_stock_demand")]

    def __str__(self):
        return self.reference


class POSStockDemandLine(TimeStampedModel):
    demand = models.ForeignKey(POSStockDemand, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_stock_demand_lines")
    demanded_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    fulfilled_quantity = models.DecimalField(**QTY)
    unit = models.CharField(max_length=24, default="Unit")
    lot_serial = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["demand", "id"]

    def __str__(self):
        return f"{self.demand} · {self.product}"


class POSInventoryTransfer(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"
        IN_TRANSIT = "in_transit", "In Transit"
        RECEIVED = "received", "Received"
        RETURNED = "returned", "Returned"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_inventory_transfers")
    demand = models.ForeignKey(POSStockDemand, on_delete=models.SET_NULL, blank=True, null=True, related_name="transfers")
    reference = models.CharField(max_length=64)
    source_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="pos_outgoing_transfers")
    destination_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="pos_incoming_transfers")
    transfer_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    dispatched_at = models.DateTimeField(blank=True, null=True)
    received_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-transfer_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "reference"], name="unique_pos_inventory_transfer")]

    def clean(self):
        if self.source_warehouse_id and self.source_warehouse_id == self.destination_warehouse_id:
            raise ValidationError({"destination_warehouse": "Destination warehouse must differ from source."})

    def __str__(self):
        return self.reference


class POSInventoryTransferLine(TimeStampedModel):
    transfer = models.ForeignKey(POSInventoryTransfer, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_inventory_transfer_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    received_quantity = models.DecimalField(**QTY)
    returned_quantity = models.DecimalField(**QTY)
    unit = models.CharField(max_length=24, default="Unit")
    lot_serial = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["transfer", "id"]

    def __str__(self):
        return f"{self.transfer} · {self.product}"


class POSMembershipAppCustomization(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name="pos_membership_theme")
    name = models.CharField(max_length=120)
    primary_color = models.CharField(max_length=16, default="#0F766E")
    secondary_color = models.CharField(max_length=16, default="#14B8A6")
    font_family = models.CharField(max_length=80, default="Inter")
    application_name = models.CharField(max_length=120)
    application_logo_url = models.URLField(max_length=500, blank=True)
    app_identifier = models.CharField(max_length=120, unique=True)
    active = models.BooleanField(default=True)
    published_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.application_name


class POSMembershipAppAccount(TimeStampedModel):
    member = models.OneToOneField(Contact, on_delete=models.CASCADE, related_name="pos_membership_account")
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40)
    password_hash = models.CharField(max_length=255)
    active = models.BooleanField(default=True)
    linked_at = models.DateTimeField(default=timezone.now)
    last_login_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.member} · {self.phone}"


class POSMembershipAppOTP(TimeStampedModel):
    account = models.ForeignKey(POSMembershipAppAccount, on_delete=models.CASCADE, related_name="otp_requests")
    phone = models.CharField(max_length=40)
    purpose = models.CharField(max_length=20, choices=(("activation", "Activation"), ("login", "Login"), ("reset", "Password Reset")), default="activation")
    otp_hash = models.CharField(max_length=255)
    active = models.BooleanField(default=True)
    verified = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(blank=True, null=True)
    delivery_status = models.CharField(max_length=16, choices=(("queued", "Queued"), ("sent", "Sent"), ("failed", "Failed")), default="queued")
    delivery_error_code = models.CharField(max_length=80, blank=True)
    message_id = models.CharField(max_length=120, blank=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.account} · {self.purpose}"


class POSRealtimeChange(TimeStampedModel):
    class Operation(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"

    profile = models.ForeignKey(POSProfile, on_delete=models.CASCADE, related_name="realtime_changes")
    model_name = models.CharField(max_length=120)
    record_identifier = models.CharField(max_length=120)
    operation = models.CharField(max_length=12, choices=Operation.choices)
    payload = models.JSONField(default=dict)
    sequence = models.PositiveBigIntegerField()
    published_at = models.DateTimeField(blank=True, null=True)
    acknowledged_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["profile", "sequence"]
        constraints = [models.UniqueConstraint(fields=["profile", "sequence"], name="unique_pos_realtime_sequence")]

    def __str__(self):
        return f"{self.profile} · {self.model_name}#{self.record_identifier}"
