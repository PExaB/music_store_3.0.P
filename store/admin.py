from django.contrib import admin
from .models import Brand, Category, Product, ProductImage, Order, OrderItem, Review
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from rangefilter.filters import DateRangeFilterBuilder
from django.utils.formats import number_format
from django.utils import timezone


REVENUE_EXPR = ExpressionWrapper(
    F('quantity') * F('price'),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)


def money(value):
    value = value or 0
    return f"{number_format(value, decimal_pos=2, use_l10n=True)} руб."


def percent_change(current, previous):
    if not previous:
        return 100 if current else 0
    return round(((current - previous) / previous) * 100, 1)


def build_sales_insights(qs):
    now = timezone.now()
    current_from = now - timezone.timedelta(days=30)
    previous_from = now - timezone.timedelta(days=60)

    current_qs = qs.filter(order__created_at__gte=current_from)
    previous_qs = qs.filter(order__created_at__gte=previous_from, order__created_at__lt=current_from)

    current = current_qs.aggregate(
        qty=Sum('quantity'),
        revenue=Sum(REVENUE_EXPR),
        orders=Count('order', distinct=True),
    )
    previous = previous_qs.aggregate(
        qty=Sum('quantity'),
        revenue=Sum(REVENUE_EXPR),
        orders=Count('order', distinct=True),
    )

    current_revenue = current['revenue'] or 0
    previous_revenue = previous['revenue'] or 0
    current_orders = current['orders'] or 0
    average_check = current_revenue / current_orders if current_orders else 0

    top_products = list(
        qs.values(
            'product_id',
            'product__name',
            'product__category__name',
            'product__brand__name',
            'product__stock_quantity',
        )
        .annotate(
            sold_qty=Sum('quantity'),
            revenue=Sum(REVENUE_EXPR),
        )
        .order_by('-sold_qty')[:5]
    )

    top_categories = list(
        qs.values('product__category__name')
        .annotate(
            sold_qty=Sum('quantity'),
            revenue=Sum(REVENUE_EXPR),
        )
        .order_by('-revenue')[:5]
    )

    top_brands = list(
        qs.values('product__brand__name')
        .annotate(
            sold_qty=Sum('quantity'),
            revenue=Sum(REVENUE_EXPR),
        )
        .order_by('-revenue')[:5]
    )

    product_forecast = []
    recent_products = {
        row['product_id']: row
        for row in current_qs.values(
            'product_id',
            'product__name',
            'product__stock_quantity',
            'product__category__name',
        ).annotate(
            recent_qty=Sum('quantity'),
            recent_revenue=Sum(REVENUE_EXPR),
        )
    }
    previous_products = {
        row['product_id']: row['previous_qty'] or 0
        for row in previous_qs.values('product_id').annotate(previous_qty=Sum('quantity'))
    }

    for product_id, row in recent_products.items():
        recent_qty = row['recent_qty'] or 0
        previous_qty = previous_products.get(product_id, 0)
        growth = recent_qty - previous_qty
        score = recent_qty * 2 + max(growth, 0) * 3
        if row['product__stock_quantity'] <= max(3, recent_qty // 2):
            score += 4

        product_forecast.append({
            'name': row['product__name'],
            'category': row['product__category__name'],
            'recent_qty': recent_qty,
            'previous_qty': previous_qty,
            'growth_percent': percent_change(recent_qty, previous_qty),
            'stock_quantity': row['product__stock_quantity'],
            'score': score,
        })

    product_forecast = sorted(product_forecast, key=lambda item: item['score'], reverse=True)[:5]

    low_stock_alerts = [
        product for product in product_forecast
        if product['stock_quantity'] <= max(3, product['recent_qty'] // 2)
    ][:3]

    recommendations = []
    revenue_growth = percent_change(current_revenue, previous_revenue)
    qty_growth = percent_change(current['qty'] or 0, previous['qty'] or 0)

    if revenue_growth > 0:
        recommendations.append(f"Выручка за последние 30 дней выросла на {revenue_growth}%. Можно усилить продвижение товаров-лидеров.")
    elif revenue_growth < 0:
        recommendations.append(f"Выручка за последние 30 дней снизилась на {abs(revenue_growth)}%. Проверьте цены, акции и наличие популярных товаров.")

    if top_categories:
        category = top_categories[0]
        recommendations.append(
            f"Категория «{category['product__category__name']}» приносит больше всего выручки: {money(category['revenue'])}."
        )

    if low_stock_alerts:
        product = low_stock_alerts[0]
        recommendations.append(
            f"У товара «{product['name']}» высокий спрос и остаток {product['stock_quantity']} шт. Стоит проверить склад."
        )

    return {
        'current_revenue': current_revenue,
        'previous_revenue': previous_revenue,
        'revenue_growth': revenue_growth,
        'qty_growth': qty_growth,
        'average_check': average_check,
        'top_products': top_products,
        'top_categories': top_categories,
        'top_brands': top_brands,
        'product_forecast': product_forecast,
        'low_stock_alerts': low_stock_alerts,
        'recommendations': recommendations,
    }


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "is_active", "website")
    list_filter = ("country", "is_active")
    search_fields = ("name", "country", "description")
    list_editable = ("is_active",)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent")

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "category", "price", "in_stock", "stock_quantity", "created_at")
    list_filter = ("brand", "category", "instrument_type", "condition", "is_active")
    search_fields = ("name", "brand__name", "description")
    list_editable = ("price", "in_stock", "stock_quantity")

admin.site.register(ProductImage)

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "full_name", "status", "payment_method",
                    "total", "created_at")
    list_filter = ("status", "payment_method", "created_at")
    search_fields = ("full_name", "user__username", "email", "phone")
    readonly_fields = ("created_at", "updated_at", "subtotal", "shipping_cost", "total")

    fieldsets = (
        ("Основное", {
            "fields": ("user", "full_name", "status", "payment_method")
        }),
        ("Контакты и доставка", {
            "fields": ("shipping_address", "phone", "email")
        }),
        ("Суммы", {
            "fields": ("subtotal", "shipping_cost", "total")
        }),
        ("Прочее", {
            "fields": ("notes", "created_at", "updated_at")
        }),
    )


@admin.register(OrderItem)
class SalesReportAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'order_created',
        'product_name',
        'product_category',
        'product_brand',
        'quantity',
        'price',
        'total_price',
    )
    list_filter = (
        ('order__created_at', DateRangeFilterBuilder()),
        'order__status',
        'product__category',
        'product__brand',
    )
    search_fields = (
        'product__name',
        'product__category__name',
        'product__brand__name',
        'order__user__username',
    )
    date_hierarchy = 'order__created_at'

    class Media:
        css = {
            'all': ('css/admin.css',)
        }

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return (
            qs.filter(order__status__in=['paid', 'shipped', 'delivered'])
              .select_related('product', 'product__category', 'product__brand', 'order')
              .annotate(order_created=F('order__created_at'),
                        total_sum=F('quantity') * F('price'))
        )

    @admin.display(ordering='order__created_at', description='Дата заказа')
    def order_created(self, obj):
        return obj.order.created_at

    @admin.display(ordering='product__name', description='Товар')
    def product_name(self, obj):
        return obj.product.name

    @admin.display(ordering='product__category__name', description='Категория')
    def product_category(self, obj):
        return obj.product.category.name

    @admin.display(ordering='product__brand__name', description='Бренд')
    def product_brand(self, obj):
        return obj.product.brand.name if obj.product.brand else '-'

    @admin.display(ordering='total_sum', description='Сумма')
    def total_price(self, obj):
        return f"{number_format(obj.total_sum, decimal_pos=2, use_l10n=True)} руб."

    # СЮДА: считаем итоги по текущему queryset
    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        try:
            qs = response.context_data['cl'].queryset
        except (AttributeError, KeyError):
            return response

        totals = qs.aggregate(
            total_qty=Sum('quantity'),
            total_revenue=Sum(REVENUE_EXPR),
        )
        response.context_data['totals'] = totals
        response.context_data['sales_insights'] = build_sales_insights(qs)
        return response
    
admin.site.register(Review)
