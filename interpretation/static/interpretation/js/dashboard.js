if (typeof BroadcastChannel !== 'undefined') {
    const bc = new BroadcastChannel('oauth_channel');
    bc.onmessage = function (event) {
        if (event.data === 'oauth_complete') {
            window.location.reload();
        }
    };
}

document.addEventListener("DOMContentLoaded", function() {
    // Auto-dismiss success alerts after 5 seconds
    const successAlerts = document.querySelectorAll('.alert-success');
    successAlerts.forEach(function(alert) {
        setTimeout(function() {
            // Apply fade transition safely
            alert.style.transition = "opacity 0.8s ease-out";
            alert.style.opacity = "0";
            
            // Wait for transition to complete then remove from DOM
            setTimeout(function() {
                if (alert.parentNode) {
                    alert.parentNode.removeChild(alert);
                } else {
                    alert.style.display = "none";
                }
            }, 800);
        }, 5000);
    });
});
