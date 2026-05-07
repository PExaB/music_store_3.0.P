from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, HttpResponseRedirect
from .models import Product, Category, Brand, Review, Order, OrderItem
from .cart import Cart
from django.db.models import Q, Count, Avg
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods


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
    products = Product.objects.filter(is_active=True)

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
    brands = Brand.objects.annotate(
        product_count=Count('products', filter=Q(products__is_active=True))
    ).filter(products__is_active=True).distinct()

    context = {
        'products': products,
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
    product = get_object_or_404(Product, id=product_id, is_active=True)

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

    context = {
        'product': product,
        'reviews': reviews,
        'avg_rating': avg_rating,
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
            cart.add(product=product)
        elif 'decrease' in request.POST:
            cart.decrease(product=product)
    
    return redirect('store:cart_detail')


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
            subtotal = cart.get_total_price()
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
                status="paid",  # или "pending"
                notes=notes,
            )

            for item in cart:
                OrderItem.objects.create(
                    order=order,
                    product=item['product'],
                    quantity=item['quantity'],
                    price=item['price'],
                )

            cart.clear()
            messages.success(request, f"Заказ №{order.id} успешно создан.")
            return redirect('store:index')

    context = {
        'cart': cart,
        'cart_items': list(cart),
        'total_price': cart.get_total_price(),
        'total_quantity': cart.get_total_quantity(),
    }
    return render(request, 'store/checkout.html', context)