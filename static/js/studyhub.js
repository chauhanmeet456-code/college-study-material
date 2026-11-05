(function () {
    'use strict';

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    function safeLink(value) {
        if (!value) return '';
        try {
            var url = new URL(value, window.location.origin);
            if (url.origin === window.location.origin && value.charAt(0) === '/') {
                return url.href;
            }
            return url.protocol === 'https:' ? url.href : '';
        } catch (_error) {
            return '';
        }
    }

    var searchInput = document.getElementById('heroSearchInput');
    var suggestionBox = document.getElementById('heroSearchSuggestions');
    var searchStatus = document.getElementById('heroSearchStatus');
    if (searchInput && suggestionBox && searchStatus) {
        var searchContainer = searchInput.closest('.hero-search-container');
        var debounceTimer;
        var requestController;

        function closeSuggestions() {
            suggestionBox.hidden = true;
            suggestionBox.replaceChildren();
            searchInput.setAttribute('aria-expanded', 'false');
            searchStatus.textContent = '';
        }

        function showSuggestions(items) {
            suggestionBox.replaceChildren();
            var shown = 0;
            items.forEach(function (item) {
                var href = safeLink(item.url);
                if (!href) return;
                var link = document.createElement('a');
                link.href = href;
                link.setAttribute('role', 'option');
                link.id = 'hero-search-suggestion-' + shown;
                link.setAttribute('aria-selected', 'false');
                var title = document.createElement('span');
                title.className = 'd-block fw-semibold';
                title.textContent = item.title || '';
                var type = document.createElement('span');
                type.className = 'small text-secondary';
                type.textContent = [item.type, item.subtitle].filter(Boolean).join(' - ')
                    || 'Study material';
                link.append(title, type);
                suggestionBox.appendChild(link);
                shown += 1;
            });
            if (shown) {
                searchStatus.textContent = '';
            } else {
                searchStatus.textContent = 'No matching study materials found.';
                var empty = document.createElement('div');
                empty.className = 'small text-muted p-3';
                empty.setAttribute('role', 'status');
                empty.textContent = searchStatus.textContent;
                suggestionBox.appendChild(empty);
            }
            suggestionBox.hidden = false;
            searchInput.setAttribute('aria-expanded', 'true');
        }

        function setLoading() {
            suggestionBox.replaceChildren();
            var loading = document.createElement('div');
            loading.className = 'small text-muted p-3';
            loading.setAttribute('role', 'status');
            loading.textContent = 'Searching...';
            suggestionBox.appendChild(loading);
            suggestionBox.hidden = false;
            searchInput.setAttribute('aria-expanded', 'true');
            searchStatus.textContent = 'Searching for study materials.';
        }

        searchInput.addEventListener('input', function () {
            window.clearTimeout(debounceTimer);
            if (requestController) requestController.abort();
            var query = searchInput.value.trim();
            if (query.length < 2) {
                closeSuggestions();
                return;
            }
            debounceTimer = window.setTimeout(function () {
                setLoading();
                requestController = new AbortController();
                var url = new URL(searchInput.dataset.suggestionsUrl, window.location.origin);
                url.searchParams.set('q', query);
                fetch(url.href, {
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' },
                    signal: requestController.signal
                }).then(function (response) {
                    if (!response.ok) throw new Error('Search suggestions unavailable');
                    return response.json();
                }).then(function (data) {
                    showSuggestions(data.suggestions || []);
                }).catch(function (error) {
                    if (error.name !== 'AbortError') closeSuggestions();
                });
            }, 250);
        });

        searchInput.addEventListener('keydown', function (event) {
            var links = suggestionBox.querySelectorAll('a[role="option"]');
            if (event.key === 'ArrowDown' && links.length) {
                event.preventDefault();
                links[0].focus();
            } else if (event.key === 'ArrowUp' && links.length) {
                event.preventDefault();
                links[links.length - 1].focus();
            } else if (event.key === 'Escape') {
                closeSuggestions();
            }
        });
        suggestionBox.addEventListener('keydown', function (event) {
            var links = Array.from(suggestionBox.querySelectorAll('a[role="option"]'));
            var index = links.indexOf(document.activeElement);
            if (event.key === 'ArrowDown' && links.length) {
                event.preventDefault();
                links[(index + 1) % links.length].focus();
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                if (index <= 0) searchInput.focus();
                else links[index - 1].focus();
            } else if (event.key === 'Escape') {
                closeSuggestions();
                searchInput.focus();
            }
        });
        document.addEventListener('click', function (event) {
            if (!searchContainer.contains(event.target)) closeSuggestions();
        });
    }

    var notificationWidget = document.getElementById('notificationWidget');
    if (notificationWidget) {
        var notificationList = document.getElementById('notificationList');
        var countBadge = document.getElementById('notificationCount');
        var pollTimer;
        var socket;

        function updateCount(count) {
            var value = Number(count) || 0;
            countBadge.textContent = value > 99 ? '99+' : String(value);
            countBadge.hidden = value === 0;
            var bell = document.getElementById('notificationBell');
            if (bell) bell.setAttribute('aria-label', value ? value + ' unread notifications' : 'Notifications');
        }

        function notificationNode(item) {
            var container = document.createElement('div');
            container.className = 'notification-entry px-3 py-2 border-bottom' + (item.is_read ? '' : ' unread');
            container.dataset.notificationId = String(item.id);
            var heading = document.createElement('strong');
            heading.className = 'd-block small';
            heading.textContent = item.title || 'Notification';
            var message = document.createElement('span');
            message.className = 'd-block small';
            message.textContent = item.message || '';
            var time = document.createElement('time');
            time.className = 'd-block small text-muted';
            time.textContent = item.created_at || '';
            container.append(heading, message, time);
            var href = safeLink(item.link);
            if (href) {
                var link = document.createElement('a');
                link.className = 'small';
                link.href = href;
                link.textContent = 'Open';
                container.appendChild(link);
            }
            if (!item.is_read) {
                var markButton = document.createElement('button');
                markButton.type = 'button';
                markButton.className = 'btn btn-sm btn-link p-0 mt-1';
                markButton.dataset.markNotification = String(item.id);
                markButton.textContent = 'Mark read';
                container.appendChild(markButton);
            }
            return container;
        }

        function renderNotifications(items) {
            notificationList.replaceChildren();
            if (!items.length) {
                var empty = document.createElement('p');
                empty.className = 'text-muted small p-3 mb-0';
                empty.textContent = 'You have no notifications yet.';
                notificationList.appendChild(empty);
                return;
            }
            items.forEach(function (item) {
                notificationList.appendChild(notificationNode(item));
            });
        }

        function refreshNotifications() {
            fetch(notificationWidget.dataset.notificationsUrl, {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' }
            }).then(function (response) {
                if (!response.ok) throw new Error('Notifications unavailable');
                return response.json();
            }).then(function (data) {
                renderNotifications(data.notifications || []);
                updateCount(data.unread_count);
                var status = document.getElementById('notificationStatus');
                if (status) status.textContent = '';
            }).catch(function () {
                var status = document.getElementById('notificationStatus');
                if (status) status.textContent = 'Notifications could not be loaded. Please try again.';
            });
        }

        function startPolling() {
            if (!pollTimer) pollTimer = window.setInterval(refreshNotifications, 60000);
        }

        function stopPolling() {
            if (pollTimer) {
                window.clearInterval(pollTimer);
                pollTimer = null;
            }
        }

        function markRead(id) {
            var readUrl = notificationWidget.dataset.markReadUrl.replace(/0\/read$/, encodeURIComponent(id) + '/read');
            return fetch(readUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken(),
                    'Accept': 'application/json'
                },
                body: '{}'
            }).then(function (response) {
                if (!response.ok) throw new Error('Could not update notification');
                return response.json();
            });
        }

        notificationWidget.addEventListener('click', function (event) {
            var button = event.target.closest('[data-mark-notification]');
            if (!button) return;
            markRead(button.dataset.markNotification).then(refreshNotifications).catch(function () {
                var status = document.getElementById('notificationStatus');
                if (status) status.textContent = 'The notification could not be marked as read.';
            });
        });
        notificationWidget.addEventListener('shown.bs.dropdown', refreshNotifications);
        document.querySelectorAll('[data-mark-notification]').forEach(function (button) {
            button.addEventListener('click', function () {
                markRead(button.dataset.markNotification).then(function () {
                    var entry = button.closest('.notification-entry');
                    if (entry) {
                        entry.classList.remove('unread');
                        button.remove();
                        var badge = entry.querySelector('.badge');
                        if (badge) {
                            badge.className = 'badge bg-secondary';
                            badge.textContent = 'Read';
                        }
                    }
                    refreshNotifications();
                }).catch(function () {
                    var status = document.getElementById('notificationStatus');
                    if (status) status.textContent = 'The notifications could not be marked as read.';
                });
            });
        });

        document.querySelectorAll('#markAllNotificationsRead').forEach(function (button) {
            button.addEventListener('click', function () {
                fetch(notificationWidget.dataset.markAllUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfToken(),
                        'Accept': 'application/json'
                    },
                    body: '{}'
                }).then(function (response) {
                    if (!response.ok) throw new Error('Could not update notifications');
                    return response.json();
                }).then(function () {
                    document.querySelectorAll('#notificationHistory .notification-entry').forEach(function (entry) {
                        entry.classList.remove('unread');
                        var badge = entry.querySelector('.badge');
                        if (badge) {
                            badge.className = 'badge bg-secondary';
                            badge.textContent = 'Read';
                        }
                        entry.querySelectorAll('[data-mark-notification]').forEach(function (item) { item.remove(); });
                    });
                    refreshNotifications();
                }).catch(function () {
                    var status = document.getElementById('notificationStatus');
                    if (status) status.textContent = 'The notifications could not be marked as read.';
                });
            });
        });

        refreshNotifications();
        if (typeof window.io === 'function') {
            try {
                socket = window.io({ withCredentials: true });
                socket.on('connect', stopPolling);
                socket.on('disconnect', startPolling);
                socket.on('connect_error', startPolling);
                socket.on('notification:new', refreshNotifications);
                window.setTimeout(function () {
                    if (!socket.connected) startPolling();
                }, 5000);
            } catch (_error) {
                startPolling();
            }
        } else {
            startPolling();
        }
    }

    var cookieModalElement = document.getElementById('cookieSettingsModal');
    var cookieModal = cookieModalElement && window.bootstrap
        ? window.bootstrap.Modal.getOrCreateInstance(cookieModalElement)
        : null;
    var cookieSettingsButton = document.querySelector('[data-cookie-settings]');
    if (cookieSettingsButton) {
        cookieSettingsButton.addEventListener('click', function () {
            if (cookieModal) cookieModal.show();
        });
    }
    document.querySelectorAll('a[href="#cookie-settings"]').forEach(function (link) {
        link.addEventListener('click', function (event) {
            event.preventDefault();
            if (cookieModal) cookieModal.show();
        });
    });

    function saveConsent(choice, preferences) {
        var meta = document.querySelector('meta[name="csrf-token"]');
        var body = document.body;
        fetch(document.body.dataset.cookieConsentUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': meta ? meta.content : '',
                'Accept': 'application/json'
            },
            body: JSON.stringify({ choice: choice, preferences: preferences })
        }).then(function (response) {
            if (!response.ok) throw new Error('Consent could not be saved');
            return response.json();
        }).then(function () {
            if (preferences) {
                localStorage.setItem('darkMode', String(body.classList.contains('dark-mode')));
            } else {
                localStorage.removeItem('darkMode');
                body.classList.remove('dark-mode');
                document.documentElement.setAttribute('data-bs-theme', 'light');
            }
            window.location.reload();
        }).catch(function () {
            var status = document.getElementById('cookieConsentStatus') ||
                document.getElementById('cookieSettingsStatus');
            if (status) status.textContent = 'Your choice could not be saved. Please try again.';
        });
    }

    document.querySelectorAll('[data-cookie-choice]').forEach(function (button) {
        button.addEventListener('click', function () {
            var accepts = button.dataset.cookieChoice === 'accept';
            saveConsent(button.dataset.cookieChoice, accepts);
        });
    });
    var saveCookieSettings = document.getElementById('saveCookieSettings');
    if (saveCookieSettings) {
        saveCookieSettings.addEventListener('click', function () {
            var preferences = document.getElementById('preferenceCookies').checked;
            saveConsent('settings', preferences);
        });
    }
})();
