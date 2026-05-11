from collections import defaultdict

from django.db import transaction

from .models import Order, Product


STOCK_DEDUCTING_STATUSES = {'paid', 'delivered'}


class StockError(ValueError):
    pass


def get_order_stock_errors(order):
    errors = []
    required_quantities = defaultdict(int)

    for item in order.items.select_related('product'):
        required_quantities[item.product_id] += item.quantity

    products = Product.objects.filter(id__in=required_quantities.keys())
    products_by_id = {product.id: product for product in products}

    for product_id, quantity in required_quantities.items():
        product = products_by_id.get(product_id)
        if not product or not product.is_active:
            errors.append("Один из товаров больше недоступен.")
            continue

        if not product.in_stock or product.stock_quantity < quantity:
            errors.append(
                f"Товара «{product.name}» недостаточно на складе. "
                f"Нужно: {quantity} шт., доступно: {product.stock_quantity} шт."
            )

    return errors


@transaction.atomic
def deduct_order_stock(order):
    order = Order.objects.select_for_update().get(pk=order.pk)

    required_quantities = defaultdict(int)
    for item in order.items.select_related('product'):
        required_quantities[item.product_id] += item.quantity

    products = Product.objects.select_for_update().filter(id__in=required_quantities.keys())
    products_by_id = {product.id: product for product in products}

    for product_id, quantity in required_quantities.items():
        product = products_by_id.get(product_id)
        if not product or not product.is_active:
            raise StockError("Один из товаров больше недоступен.")

        if not product.in_stock or product.stock_quantity < quantity:
            raise StockError(
                f"Товара «{product.name}» недостаточно на складе. "
                f"Нужно: {quantity} шт., доступно: {product.stock_quantity} шт."
            )

    for product_id, quantity in required_quantities.items():
        product = products_by_id[product_id]
        product.stock_quantity -= quantity
        product.in_stock = product.stock_quantity > 0
        product.save(update_fields=['stock_quantity', 'in_stock'])


@transaction.atomic
def restore_order_stock(order):
    order = Order.objects.select_for_update().get(pk=order.pk)

    returned_quantities = defaultdict(int)
    for item in order.items.select_related('product'):
        returned_quantities[item.product_id] += item.quantity

    products = Product.objects.select_for_update().filter(id__in=returned_quantities.keys())
    products_by_id = {product.id: product for product in products}

    for product_id, quantity in returned_quantities.items():
        product = products_by_id.get(product_id)
        if not product:
            continue

        product.stock_quantity += quantity
        product.in_stock = True
        product.save(update_fields=['stock_quantity', 'in_stock'])


def sync_order_stock_for_status(order, previous_status=None):
    previous_deducts_stock = previous_status in STOCK_DEDUCTING_STATUSES
    current_deducts_stock = order.status in STOCK_DEDUCTING_STATUSES

    if not previous_deducts_stock and current_deducts_stock:
        deduct_order_stock(order)
    elif previous_deducts_stock and not current_deducts_stock:
        restore_order_stock(order)
