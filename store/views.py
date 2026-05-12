from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, HttpResponseRedirect
from .models import Product, Category, Brand, Review, Order, OrderItem
from .cart import Cart
from .inventory import StockError, sync_order_stock_for_status
from django.db import transaction
from django.db.models import Q, Count, Avg
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator


def index(request):
    """Главная страница"""
    featured_products = Product.objects.filter(is_active=True)[:8]
    categories = Category.objects.all()
    brands = Brand.objects.filter(is_active=True)  # активные бренды для карусели
    
    context = {
        'featured_products': featured_products,
        'categories': categories,
        'brands': brands,
    }
    return render(request, 'store/index.html', context)

def product_list(request):
    """Список всех товаров с фильтрацией по категориям и брендам"""
    # Получаем списки id из GET
    category_ids = request.GET.getlist('category')   # ['1', '2', ...]
    brand_ids = request.GET.getlist('brand')         # ['1', '3', ...]

    # Базовый queryset
    products = Product.objects.filter(is_active=True).select_related('brand', 'category')

    # Фильтрация по категориям (множественный выбор)
    if category_ids:
        products = products.filter(category_id__in=category_ids)

    # Фильтрация по брендам (множественный выбор)
    if brand_ids:
        products = products.filter(brand_id__in=brand_ids)

    # Текущие выбранные категории/бренды для заголовка
    current_categories = Category.objects.filter(id__in=category_ids)
    current_brands = Brand.objects.filter(id__in=brand_ids)

    # Категории с количеством активных товаров (без учёта фильтра по брендам)
    categories = Category.objects.annotate(
        product_count=Count('products', filter=Q(products__is_active=True))
    )

    # Бренды с количеством активных товаров
    brands = Brand.objects.filter(is_active=True).annotate(
        product_count=Count('products', filter=Q(products__is_active=True))
    ).distinct()

    found_products_count = products.count()
    paginator = Paginator(products, 9)
    page_obj = paginator.get_page(request.GET.get('page'))

    pagination_query = request.GET.copy()
    pagination_query.pop('page', None)
    pagination_query = pagination_query.urlencode()

    context = {
        'products': page_obj.object_list,
        'page_obj': page_obj,
        'paginator': paginator,
        'pagination_query': pagination_query,
        'found_products_count': found_products_count,
        'all_products_count': Product.objects.filter(is_active=True).count(),
        'categories': categories,
        'brands': brands,
        'current_categories': current_categories,
        'current_brands': current_brands,
        'selected_category_ids': category_ids,  # список строк
        'selected_brand_ids': brand_ids,        # список строк
    }
    return render(request, 'store/product_list.html', context)


def product_detail(request, product_id):
    """Детальная страница товара с отзывами"""
    product = get_object_or_404(
        Product.objects.select_related('brand', 'category').prefetch_related('images'),
        id=product_id,
        is_active=True
    )

    # список одобренных отзывов + средний рейтинг
    reviews = product.reviews.filter(is_approved=True).select_related('user')
    avg_rating = reviews.aggregate(avg=Avg('rating'))['avg'] or 0

    # обработка отправки нового отзыва
    if request.method == 'POST' and request.user.is_authenticated:
        rating = int(request.POST.get('rating', 0))
        comment = request.POST.get('comment', '').strip()

        if 1 <= rating <= 5 and comment:
            # один отзыв на пользователя (unique_together)
            Review.objects.update_or_create(
                product=product,
                user=request.user,
                defaults={
                    'rating': rating,
                    'comment': comment,
                    # можно автоподтверждать
                    'is_approved': True,
                }
            )
            return redirect('store:product_detail', product_id=product.id)

    specs = [
        ("Категория", product.category.name),
        ("Бренд", product.brand.name if product.brand else None),
        ("Уровень подготовки", product.get_skill_level_display() if product.skill_level else None),
        ("Состояние", product.get_condition_display()),
        ("Наличие", f"В наличии, {product.stock_quantity} шт." if product.in_stock else "Нет в наличии"),
        ("Вес", f"{product.weight} кг" if product.weight else None),
        ("Габариты", product.dimensions or None),
    ]

    specs = [(label, value) for label, value in specs if value]
    feature_lines = [
        line.strip(" •-")
        for line in (product.features or "").splitlines()
        if line.strip(" •-")
    ]

    instrument_tips = {
        "guitar": [
            "Проверьте удобство грифа и высоту струн под свой стиль игры.",
            "Для электрогитары заранее подберите кабель, медиаторы и подходящий усилитель.",
            "Новичкам лучше выбирать инструмент с понятной настройкой и стабильным строем.",
        ],
        "piano": [
            "Обратите внимание на количество клавиш, чувствительность к нажатию и наличие педали.",
            "Для занятий дома полезны выход на наушники и компактная стойка.",
            "Если инструмент нужен для обучения, важны метроном и обучающие режимы.",
        ],
        "drums": [
            "Уточните размеры установки и место, где она будет стоять.",
            "Для старта пригодятся палочки, стул и тренировочный пэд.",
            "Для квартиры лучше рассмотреть электронные барабаны или демпферы.",
        ],
        "violin": [
            "Проверьте размер инструмента, особенно если покупаете для ребенка.",
            "Смычок, канифоль и чехол лучше подготовить сразу.",
            "После покупки полезна базовая настройка у мастера.",
        ],
        "wind": [
            "Учитывайте материал корпуса, комплектацию и удобство обслуживания.",
            "Для регулярной игры понадобится набор для ухода.",
            "Новичкам лучше выбирать модели с простой механикой и стабильной интонацией.",
        ],
        "equipment": [
            "Проверьте совместимость с вашим инструментом и нужную мощность.",
            "Для дома важны выход на наушники и компактный размер.",
            "Для репетиций и сцены лучше брать запас по громкости.",
        ],
        "accessories": [
            "Проверьте совместимость аксессуара с вашим инструментом.",
            "Для расходников удобно брать запас на несколько месяцев.",
            "Если сомневаетесь, напишите AI-консультанту, он подберет пару к инструменту.",
        ],
    }.get(product.instrument_type, [])

    related_products = Product.objects.filter(
        is_active=True,
        in_stock=True,
        category=product.category,
    ).exclude(id=product.id).select_related('brand', 'category')[:4]

    context = {
        'product': product,
        'reviews': reviews,
        'avg_rating': avg_rating,
        'specs': specs,
        'feature_lines': feature_lines,
        'instrument_tips': instrument_tips,
        'related_products': related_products,
    }
    return render(request, 'store/product_detail.html', context)


# Views для корзины
def cart_detail(request):
    """Детальная страница корзины"""
    cart = Cart(request)
    
    context = {
        'cart': cart,
        'cart_items': list(cart),  
        'total_price': cart.get_total_price(),
        'total_quantity': cart.get_total_quantity(),
    }
    return render(request, 'store/cart.html', context)

def cart_add(request, product_id):
    """Добавление товара в корзину"""
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id, is_active=True)

    current_quantity = cart.cart.get(str(product.id), {}).get('quantity', 0)
    if not product.in_stock or product.stock_quantity <= current_quantity:
        messages.error(request, "На складе нет нужного количества этого товара.")
        referer = request.META.get('HTTP_REFERER', '/')
        return redirect(referer)

    cart.add(product=product)
    
    referer = request.META.get('HTTP_REFERER', '/')
    return redirect(referer)

def cart_remove(request, product_id):
    """Удаление товара из корзины"""
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    cart.remove(product)
    return redirect('store:cart_detail')

def cart_update(request, product_id):
    """Обновление количества товара в корзине"""
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    
    if request.method == 'POST':
        if 'increase' in request.POST:
            current_quantity = cart.cart.get(str(product.id), {}).get('quantity', 0)
            if not product.in_stock or product.stock_quantity <= current_quantity:
                messages.error(request, "На складе нет нужного количества этого товара.")
                return redirect('store:cart_detail')
            cart.add(product=product)
        elif 'decrease' in request.POST:
            cart.decrease(product=product)
    
    return redirect('store:cart_detail')


@login_required
def order_history(request):
    orders = (
        Order.objects
        .filter(user=request.user)
        .prefetch_related('items__product', 'items__product__category', 'items__product__brand')
        .order_by('-created_at')
    )

    status_badges = {
        'pending': 'secondary',
        'paid': 'primary',
        'delivered': 'success',
        'cancelled': 'danger',
    }

    context = {
        'orders': orders,
        'status_badges': status_badges,
    }
    return render(request, 'store/order_history.html', context)


@login_required
@require_http_methods(["POST"])
def cancel_order(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    if order.status not in {'pending', 'paid'}:
        messages.error(request, "Этот заказ уже нельзя отменить.")
        return redirect('store:order_history')

    previous_status = order.status
    order.status = 'cancelled'
    order.save(update_fields=['status', 'updated_at'])
    sync_order_stock_for_status(order, previous_status=previous_status)

    messages.success(request, f"Заказ №{order.id} отменен.")
    return redirect('store:order_history')


@login_required
@require_http_methods(["GET", "POST"])
def checkout(request):
    cart = Cart(request)

    if len(cart) == 0:
        messages.info(request, "Корзина пуста, добавьте товары перед оформлением заказа.")
        return redirect('store:product_list')

    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        shipping_address = request.POST.get('shipping_address', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        payment_method = request.POST.get('payment_method', '').strip()
        notes = request.POST.get('notes', '').strip()

        if not full_name or not shipping_address or not phone or not email or not payment_method:
            messages.error(request, "Пожалуйста, заполните все обязательные поля.")
        else:
            try:
                with transaction.atomic():
                    cart_data = cart.cart.copy()
                    product_ids = cart_data.keys()
                    products = {
                        str(product.id): product
                        for product in Product.objects.select_for_update().filter(id__in=product_ids)
                    }

                    order_items = []
                    subtotal = 0

                    for product_id, item in cart_data.items():
                        product = products.get(product_id)
                        quantity = item['quantity']

                        if not product or not product.is_active:
                            raise ValueError("Один из товаров больше недоступен.")

                        if not product.in_stock or product.stock_quantity < quantity:
                            raise ValueError(
                                f"Товара «{product.name}» недостаточно на складе. Доступно: {product.stock_quantity} шт."
                            )

                        price = product.price
                        subtotal += price * quantity
                        order_items.append({
                            'product': product,
                            'quantity': quantity,
                            'price': price,
                        })

                    shipping_cost = 0

                    order = Order.objects.create(
                        user=request.user,
                        full_name=full_name,
                        shipping_address=shipping_address,
                        phone=phone,
                        email=email,
                        payment_method=payment_method,
                        subtotal=subtotal,
                        shipping_cost=shipping_cost,
                        status="paid",
                        notes=notes,
                    )

                    for item in order_items:
                        OrderItem.objects.create(
                            order=order,
                            product=item['product'],
                            quantity=item['quantity'],
                            price=item['price'],
                        )

                    sync_order_stock_for_status(order, previous_status='pending')
            except (ValueError, StockError) as error:
                messages.error(request, str(error))
            else:
                cart.clear()
                messages.success(request, f"Заказ №{order.id} успешно оформлен и оплачен.")
                return redirect('store:order_history')

    context = {
        'cart': cart,
        'cart_items': list(cart),
        'total_price': cart.get_total_price(),
        'total_quantity': cart.get_total_quantity(),
    }
    return render(request, 'store/checkout.html', context)
