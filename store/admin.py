from django.contrib import admin, messages
from django import forms
from decimal import Decimal
import hashlib
from chatbot.gigachat_service import GigaChatService
from .models import Brand, Category, Product, ProductImage, Order, OrderItem, Review
from .inventory import STOCK_DEDUCTING_STATUSES, StockError, get_order_stock_errors, sync_order_stock_for_status
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from rangefilter.filters import DateRangeFilterBuilder
from django.utils.formats import number_format
from django.utils import timezone
from django.core.cache import cache
from django.shortcuts import redirect


REVENUE_EXPR = ExpressionWrapper(
    F('quantity') * F('price'),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)
SALES_ANALYTICS_CACHE_TIMEOUT = 60 * 60
SALES_AI_CACHE_TIMEOUT = 60 * 60 * 12


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
    trend_from = now - timezone.timedelta(days=56)

    sales_qs = qs.filter(order__status__in=['paid', 'delivered'])
    cancelled_qs = qs.filter(order__status='cancelled')
    current_qs = sales_qs.filter(order__created_at__gte=current_from)
    previous_qs = sales_qs.filter(order__created_at__gte=previous_from, order__created_at__lt=current_from)

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

    cancelled = cancelled_qs.aggregate(
        qty=Sum('quantity'),
        revenue=Sum(REVENUE_EXPR),
        orders=Count('order', distinct=True),
    )
    active_orders_count = sales_qs.values('order_id').distinct().count()
    cancelled_orders_count = cancelled['orders'] or 0
    all_orders_count = active_orders_count + cancelled_orders_count
    cancellation_rate = round((cancelled_orders_count / all_orders_count) * 100, 1) if all_orders_count else 0

    top_products = list(
        sales_qs.values(
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
        sales_qs.values('product__category__name')
        .annotate(
            sold_qty=Sum('quantity'),
            revenue=Sum(REVENUE_EXPR),
        )
        .order_by('-revenue')[:5]
    )

    top_brands = list(
        sales_qs.values('product__brand__name')
        .annotate(
            sold_qty=Sum('quantity'),
            revenue=Sum(REVENUE_EXPR),
        )
        .order_by('-revenue')[:5]
    )

    cancelled_products = list(
        cancelled_qs.values(
            'product__name',
            'product__category__name',
            'product__brand__name',
        )
        .annotate(
            cancelled_qty=Sum('quantity'),
            cancelled_revenue=Sum(REVENUE_EXPR),
            cancelled_orders=Count('order', distinct=True),
        )
        .order_by('-cancelled_qty')[:5]
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

    trend_map = {}
    for row in sales_qs.filter(order__created_at__gte=trend_from).values(
        'order__created_at',
    ).annotate(
        qty=Sum('quantity'),
        revenue=Sum(REVENUE_EXPR),
    ):
        order_date = row['order__created_at']
        week_start = (order_date - timezone.timedelta(days=order_date.weekday())).date()
        if week_start not in trend_map:
            trend_map[week_start] = {'qty': 0, 'revenue': Decimal('0')}
        trend_map[week_start]['qty'] += row['qty'] or 0
        trend_map[week_start]['revenue'] += row['revenue'] or 0

    sales_trend = []
    for week_number in range(7, -1, -1):
        week_date = (now - timezone.timedelta(days=week_number * 7)).date()
        week_start = week_date - timezone.timedelta(days=week_date.weekday())
        values = trend_map.get(week_start, {'qty': 0, 'revenue': Decimal('0')})
        sales_trend.append({
            'label': week_start.strftime('%d.%m'),
            'qty': values['qty'],
            'revenue': values['revenue'],
        })

    max_week_revenue = max((item['revenue'] for item in sales_trend), default=0) or 1
    for item in sales_trend:
        item['height'] = max(8, int((item['revenue'] / max_week_revenue) * 100)) if item['revenue'] else 8

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

    if cancelled_orders_count:
        recommendations.append(
            f"Отменено {cancelled_orders_count} заказов ({cancellation_rate}% от заказов в отчете) на сумму {money(cancelled['revenue'])}."
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
        'cancelled_qty': cancelled['qty'] or 0,
        'cancelled_revenue': cancelled['revenue'] or 0,
        'cancelled_orders_count': cancelled_orders_count,
        'cancellation_rate': cancellation_rate,
        'cancelled_products': cancelled_products,
        'product_forecast': product_forecast,
        'low_stock_alerts': low_stock_alerts,
        'sales_trend': sales_trend,
        'recommendations': recommendations,
    }


def build_llm_sales_payload(insights):
    return {
        'period': 'последние 30 дней, сравнение с предыдущими 30 днями',
        'current_revenue': insights['current_revenue'],
        'previous_revenue': insights['previous_revenue'],
        'revenue_growth_percent': insights['revenue_growth'],
        'quantity_growth_percent': insights['qty_growth'],
        'average_check': insights['average_check'],
        'cancelled_orders_count': insights['cancelled_orders_count'],
        'cancelled_qty': insights['cancelled_qty'],
        'cancelled_revenue': insights['cancelled_revenue'],
        'cancellation_rate_percent': insights['cancellation_rate'],
        'top_products': [
            {
                'name': item['product__name'],
                'category': item['product__category__name'],
                'brand': item['product__brand__name'] or 'Без бренда',
                'sold_qty': item['sold_qty'],
                'revenue': item['revenue'],
                'stock_quantity': item['product__stock_quantity'],
            }
            for item in insights['top_products']
        ],
        'top_categories': [
            {
                'name': item['product__category__name'],
                'sold_qty': item['sold_qty'],
                'revenue': item['revenue'],
            }
            for item in insights['top_categories']
        ],
        'top_brands': [
            {
                'name': item['product__brand__name'] or 'Без бренда',
                'sold_qty': item['sold_qty'],
                'revenue': item['revenue'],
            }
            for item in insights['top_brands']
        ],
        'cancelled_products': [
            {
                'name': item['product__name'],
                'category': item['product__category__name'],
                'brand': item['product__brand__name'] or 'Без бренда',
                'cancelled_qty': item['cancelled_qty'],
                'cancelled_orders': item['cancelled_orders'],
                'cancelled_revenue': item['cancelled_revenue'],
            }
            for item in insights['cancelled_products']
        ],
        'demand_forecast': insights['product_forecast'],
        'low_stock_alerts': insights['low_stock_alerts'],
        'sales_trend': insights['sales_trend'],
        'rule_based_recommendations': insights['recommendations'],
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


class OrderAdminForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')

        previous_status = None
        if self.instance.pk:
            previous_status = Order.objects.filter(pk=self.instance.pk).values_list('status', flat=True).first()

        if previous_status not in STOCK_DEDUCTING_STATUSES and status in STOCK_DEDUCTING_STATUSES:
            errors = get_order_stock_errors(self.instance)
            if errors:
                raise forms.ValidationError(errors)

        return cleaned_data


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    form = OrderAdminForm
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

    def save_model(self, request, obj, form, change):
        previous_status = None
        if change:
            previous_status = Order.objects.filter(pk=obj.pk).values_list('status', flat=True).first()

        super().save_model(request, obj, form, change)
        try:
            sync_order_stock_for_status(obj, previous_status=previous_status)
        except StockError as error:
            self.message_user(request, str(error), level='ERROR')


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
            qs.filter(order__status__in=['paid', 'delivered', 'cancelled'])
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

    def _sales_cache_keys(self, request):
        query = request.GET.copy()
        query.pop('p', None)
        query.pop('o', None)
        query.pop('_changelist_filters', None)
        raw_key = query.urlencode()
        digest = hashlib.md5(raw_key.encode('utf-8')).hexdigest()
        return {
            'analytics': f'admin:sales_analytics:{digest}',
            'ai': f'admin:sales_ai:{digest}',
        }

    def _build_sales_report_cache(self, qs):
        active_qs = qs.filter(order__status__in=['paid', 'delivered'])
        cancelled_qs = qs.filter(order__status='cancelled')
        sales_insights = build_sales_insights(qs)

        return {
            'totals': active_qs.aggregate(
                total_qty=Sum('quantity'),
                total_revenue=Sum(REVENUE_EXPR),
            ),
            'cancelled_totals': cancelled_qs.aggregate(
                total_qty=Sum('quantity'),
                total_revenue=Sum(REVENUE_EXPR),
            ),
            'sales_insights': sales_insights,
        }

    # СЮДА: показываем кэшированный отчет и обновляем AI только по кнопке
    def changelist_view(self, request, extra_context=None):
        refresh_ai = request.method == 'POST' and request.POST.get('refresh_sales_ai')
        if refresh_ai:
            cache_keys = self._sales_cache_keys(request)
            try:
                cl = self.get_changelist_instance(request)
                report_cache = self._build_sales_report_cache(cl.queryset)
                sales_insights = report_cache['sales_insights']
                llm_sales_summary = GigaChatService().analyze_sales(
                    build_llm_sales_payload(sales_insights)
                )
            except Exception:
                messages.error(request, "Не удалось обновить AI-анализ. Отчет из кэша остался доступен.")
                return redirect(request.get_full_path())

            cache.set(cache_keys['analytics'], report_cache, SALES_ANALYTICS_CACHE_TIMEOUT)
            cache.set(cache_keys['ai'], llm_sales_summary, SALES_AI_CACHE_TIMEOUT)
            messages.success(request, "AI-анализ продаж обновлен и сохранен в кэш.")
            return redirect(request.get_full_path())

        response = super().changelist_view(request, extra_context=extra_context)
        try:
            qs = response.context_data['cl'].queryset
        except (AttributeError, KeyError):
            return response

        cache_keys = self._sales_cache_keys(request)
        report_cache = cache.get(cache_keys['analytics'])
        llm_sales_summary = cache.get(cache_keys['ai'])

        if report_cache is None:
            report_cache = self._build_sales_report_cache(qs)
            cache.set(cache_keys['analytics'], report_cache, SALES_ANALYTICS_CACHE_TIMEOUT)
            llm_sales_summary = None

        response.context_data['totals'] = report_cache['totals']
        response.context_data['cancelled_totals'] = report_cache['cancelled_totals']
        response.context_data['sales_insights'] = report_cache['sales_insights']
        response.context_data['llm_sales_summary'] = llm_sales_summary
        response.context_data['sales_ai_cached'] = llm_sales_summary is not None
        return response
    
admin.site.register(Review)
