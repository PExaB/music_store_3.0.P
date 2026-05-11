// store/static/js/index_brand_carousel.js
document.addEventListener('DOMContentLoaded', function() {
    const prevBtn = document.querySelector('.carousel-btn.prev');
    const nextBtn = document.querySelector('.carousel-btn.next');
    const carousel = document.querySelector('.brand-carousel-inner');

    if (!carousel) return; // если секция отсутствует, выходим

    const scrollAmount = 360; // ширина 3 элементов + gap

    prevBtn.addEventListener('click', () => {
        carousel.scrollBy({ left: -scrollAmount, behavior: 'smooth' });
    });

    nextBtn.addEventListener('click', () => {
        carousel.scrollBy({ left: scrollAmount, behavior: 'smooth' });
    });
});