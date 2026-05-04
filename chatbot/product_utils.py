# chatbot/product_utils.py
from django.db.models import Q
from store.models import Product, Category   # импорт из приложения store

QUERY_TO_INSTRUMENT = {
    # Гитары (обычные)
    'гитар': 'guitar',
    'акуст': 'guitar',
    'электр': 'guitar',
    # Бас-гитары
    'бас-гитар': 'guitar',
    'бас гитар': 'guitar',
    'басов': 'guitar',   # "басовая"
    # Клавишные
    'пианин': 'piano',
    'клавиш': 'piano',
    'роял': 'piano',
    # Ударные
    'барабан': 'drums',
    'ударн': 'drums',
    # Струнные
    'скрипк': 'violin',
    'струн': 'violin',
    # Духовые
    'духов': 'wind',
    # Оборудование / усилители
    'усилител': 'equipment',
    'комбик': 'equipment',
    'комбо': 'equipment',
    'кабинет': 'equipment',
    # Аксессуары
    'аксессуар': 'accessories',
    'смыч': 'accessories',
    'палоч': 'accessories',
    'стойк': 'accessories',
}

def search_products(
    query: str = None,
    category: str = None,
    budget_max: float = None,
    skill_level: str = None,
    instrument_type: str = None,
    exclude_instrument_types: list = None,
    electric_guitar_only: bool = False,
    guitar_subtype: str = None,
):
    print(f">>> search_products args: query={query}, category={category}, budget_max={budget_max}, skill_level={skill_level}, instrument_type={instrument_type}, guitar_subtype={guitar_subtype}")

    filters = Q(is_active=True, in_stock=True)

    # --- QUERY ---
    if query:
        query_lower = query.lower()

        q_filter = (
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(brand__name__icontains=query) |
            Q(category__name__icontains=query)
        )

        # Определяем instrument_type ТОЛЬКО через AND
        detected_types = set()
        for key, val in QUERY_TO_INSTRUMENT.items():
            if key in query_lower:
                detected_types.add(val)

        # если нашли один тип — применяем
        if detected_types and not instrument_type:
            if len(detected_types) == 1:
                instrument_type = list(detected_types)[0]

        filters &= q_filter

    # --- CATEGORY ---
    if category:
        filters &= Q(category__name__icontains=category)

    # --- INSTRUMENT TYPE (главный фильтр) ---
    if instrument_type:
        filters &= Q(instrument_type=instrument_type)

    # --- SKILL LEVEL ---
    if skill_level and instrument_type != 'equipment':
        filters &= Q(skill_level=skill_level)

    # --- ГИТАРЫ (строгая логика) ---
    if instrument_type == 'guitar':
        query_lower = query.lower() if query else ""

        # авто-определение subtype
        if not guitar_subtype:
            if any(word in query_lower for word in ['акустич', 'классич']):
                guitar_subtype = 'acoustic'
            elif any(word in query_lower for word in ['бас']):
                guitar_subtype = 'bass'
            elif any(word in query_lower for word in ['электр', 'комбик', 'усилител']):
                guitar_subtype = 'electric'

        if guitar_subtype == 'electric':
            filters &= ~Q(category__name__icontains='акуст')
            filters &= ~Q(category__name__icontains='классич')
            filters &= ~Q(category__name__icontains='бас')
            filters &= ~Q(name__icontains='бас')

        elif guitar_subtype == 'acoustic':
            filters &= (
                Q(category__name__icontains='акуст') |
                Q(category__name__icontains='классич')
            )
            filters &= ~Q(category__name__icontains='бас')

        elif guitar_subtype == 'bass':
            filters &= (
                Q(category__name__icontains='бас') |
                Q(name__icontains='бас')
            )

        elif electric_guitar_only:
            filters &= ~Q(category__name__icontains='акуст')
            filters &= ~Q(category__name__icontains='классич')
            filters &= ~Q(category__name__icontains='бас')

    # --- EXCLUDE ---
    if exclude_instrument_types:
        filters &= ~Q(instrument_type__in=exclude_instrument_types)

    # --- QUERY ---
    products = Product.objects.filter(filters).select_related('brand', 'category')

    # --- BUDGET ---
    if budget_max is not None:
        products = products.filter(price__lte=budget_max)

    products = products[:10]

    # --- RESULT ---
    result = []
    for p in products:
        brand_name = p.brand.name if p.brand else ""
        # Собираем полное имя без дублирования бренда
        if p.brand and p.brand.name.lower() not in p.name.lower():
            full_name = f"{p.brand.name} {p.name}".strip()
        else:
            full_name = p.name.strip()

        result.append({
            "id": p.id,
            "name": full_name,
            "price": str(p.price),
            "category": p.category.name,
            "instrument_type": p.instrument_type,
            "description": p.description[:200] + "..." if len(p.description) > 200 else p.description,
            "skill_level": p.skill_level,
            "url": f"/products/{p.id}/",
            "features": p.features[:100] + "..." if p.features and len(p.features) > 100 else p.features,
            "image": p.image.url if p.image else None,
        })

    print(f">>> Final filters: {filters}")
    print(f">>> Products found: {products.count()}")

    return result

def get_product_details(product_id: int):
    """
    Получение детальной информации о товаре по ID.
    """
    try:
        p = Product.objects.select_related('brand', 'category').get(pk=product_id, is_active=True)
    except Product.DoesNotExist:
        return {"error": f"Товар с ID {product_id} не найден"}

    brand_name = p.brand.name if p.brand else ""
    # Собираем полное имя без дублирования бренда
    if p.brand and p.brand.name.lower() not in p.name.lower():
        full_name = f"{p.brand.name} {p.name}".strip()
    else:
        full_name = p.name.strip()
    return {
        "id": p.id,
        "name": full_name,
        "price": str(p.price),
        "old_price": str(p.old_price) if p.old_price else None,
        "category": p.category.name,
        "description": p.description,
        "features": p.features,
        "skill_level": p.skill_level,
        "in_stock": p.in_stock,
        "stock_quantity": p.stock_quantity,
        "url": f"/products/{p.id}/",
    }