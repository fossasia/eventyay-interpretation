(function() {
    // If the page is running inside an iframe, add the 'embedded-view' class
    // This class is used by embedded.css to hide the global navigation and sidebar
    if (window.self !== window.top) {
        document.documentElement.classList.add('embedded-view');
    }
})();
