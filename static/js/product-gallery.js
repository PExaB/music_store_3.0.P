// Галерея изображений товара
document.addEventListener('DOMContentLoaded', function () {
    const mainImage = document.getElementById('mainProductImage');
    const thumbsContainer = document.getElementById('productThumbs');
    const thumbnails = document.querySelectorAll('.product-thumb');

    const scrollUp = document.getElementById('thumbScrollUp');
    const scrollDown = document.getElementById('thumbScrollDown');

    if (!mainImage || thumbnails.length === 0) {
        return;
    }

    thumbnails.forEach(function (thumb) {
        thumb.addEventListener('click', function () {
            const newImage = this.dataset.image;
            const img = this.querySelector('img');

            if (!newImage) {
                return;
            }

            mainImage.src = newImage;

            if (img && img.alt) {
                mainImage.alt = img.alt;
            }

            thumbnails.forEach(function (item) {
                item.classList.remove('active');
            });

            this.classList.add('active');
        });
    });

    if (scrollUp && thumbsContainer) {
        scrollUp.addEventListener('click', function () {
            thumbsContainer.scrollBy({
                top: -110,
                behavior: 'smooth'
            });
        });
    }

    if (scrollDown && thumbsContainer) {
        scrollDown.addEventListener('click', function () {
            thumbsContainer.scrollBy({
                top: 110,
                behavior: 'smooth'
            });
        });
    }
});