document.addEventListener('DOMContentLoaded', function() {
    const footer = document.querySelector('.site-footer');
    if (!footer) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                footer.classList.add('visible');
            }
        });
    }, { threshold: 0.1 });

    observer.observe(footer);
});