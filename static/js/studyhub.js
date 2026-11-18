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
        var loadingTimer;
        var requestController;

        function closeSuggestions() {
            if (requestController) {
                requestController.abort();
                requestController = null;
            }
            window.clearTimeout(loadingTimer);
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
            for (var index = 0; index < 2; index += 1) {
                var row = document.createElement('div');
                row.className = 'studyhub-suggestion-skeleton';
                row.setAttribute('aria-hidden', 'true');
                var title = document.createElement('span');
                title.className = 'studyhub-skeleton studyhub-skeleton-line';
                var detail = document.createElement('span');
                detail.className = 'studyhub-skeleton studyhub-skeleton-line';
                row.append(title, detail);
                suggestionBox.appendChild(row);
            }
            suggestionBox.hidden = false;
            searchInput.setAttribute('aria-expanded', 'true');
            searchStatus.textContent = 'Searching for study materials.';
        }

        function showSuggestionError() {
            suggestionBox.replaceChildren();
            var message = document.createElement('p');
            message.className = 'small text-danger px-3 pt-3 mb-2';
            message.setAttribute('role', 'alert');
            message.textContent = 'Suggestions could not be loaded.';
            var retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'btn btn-sm btn-outline-primary mx-3 mb-3';
            retry.dataset.retrySuggestions = 'true';
            retry.textContent = 'Try again';
            suggestionBox.append(message, retry);
            suggestionBox.hidden = false;
            searchInput.setAttribute('aria-expanded', 'true');
            searchStatus.textContent = 'Suggestions could not be loaded.';
        }

        function loadSuggestions(query) {
            if (requestController) requestController.abort();
            window.clearTimeout(loadingTimer);
            requestController = new AbortController();
            var controller = requestController;
            var url = new URL(searchInput.dataset.suggestionsUrl, window.location.origin);
            url.searchParams.set('q', query);
            loadingTimer = window.setTimeout(setLoading, 120);
            fetch(url.href, {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' },
                signal: controller.signal
            }).then(function (response) {
                if (!response.ok) throw new Error('Search suggestions unavailable');
                return response.json();
            }).then(function (data) {
                if (controller !== requestController) return;
                window.clearTimeout(loadingTimer);
                showSuggestions(data.suggestions || []);
            }).catch(function (error) {
                if (error.name === 'AbortError' || controller !== requestController) return;
                window.clearTimeout(loadingTimer);
                showSuggestionError();
            });
        }

        searchInput.addEventListener('input', function () {
            window.clearTimeout(debounceTimer);
            if (requestController) requestController.abort();
            window.clearTimeout(loadingTimer);
            var query = searchInput.value.trim();
            if (query.length < 2) {
                closeSuggestions();
                return;
            }
            debounceTimer = window.setTimeout(function () {
                loadSuggestions(query);
            }, 250);
        });

        suggestionBox.addEventListener('click', function (event) {
            var retry = event.target.closest('[data-retry-suggestions]');
            if (retry) loadSuggestions(searchInput.value.trim());
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

    var searchForm = document.querySelector('[data-studyhub-ajax-search]');
    if (searchForm) {
        var resultGrid = document.getElementById('searchResultGrid');
        var resultSummary = document.getElementById('searchResultSummary');
        var resultStatus = document.getElementById('searchResultsStatus');
        var searchController;
        var searchLoadingTimer;

        function makeSkeletonCard() {
            var column = document.createElement('div');
            column.className = 'col-md-6 col-lg-4';
            column.setAttribute('aria-hidden', 'true');
            var card = document.createElement('div');
            card.className = 'card material-card studyhub-skeleton-card';
            var media = document.createElement('div');
            media.className = 'studyhub-skeleton studyhub-skeleton-media';
            var body = document.createElement('div');
            body.className = 'studyhub-skeleton-body';
            [
                'studyhub-skeleton studyhub-skeleton-title',
                'studyhub-skeleton studyhub-skeleton-copy',
                'studyhub-skeleton studyhub-skeleton-copy',
                'studyhub-skeleton studyhub-skeleton-footer'
            ].forEach(function (className) {
                var part = document.createElement('span');
                part.className = className;
                body.appendChild(part);
            });
            card.append(media, body);
            column.appendChild(card);
            return column;
        }

        function showSearchSkeletons() {
            resultGrid.replaceChildren();
            for (var index = 0; index < 6; index += 1) {
                resultGrid.appendChild(makeSkeletonCard());
            }
            resultGrid.setAttribute('aria-busy', 'true');
            resultStatus.textContent = 'Loading search results.';
        }

        function createMaterialCard(material) {
            var column = document.createElement('div');
            column.className = 'col-md-6 col-lg-4';
            var card = document.createElement('div');
            card.className = 'card material-card h-100';
            var image = document.createElement('div');
            image.className = 'card-img-top';
            var icon = document.createElement('i');
            icon.className = 'fas fa-file-alt';
            icon.setAttribute('aria-hidden', 'true');
            image.appendChild(icon);

            var body = document.createElement('div');
            body.className = 'card-body d-flex flex-column';
            var title = document.createElement('h5');
            title.className = 'card-title';
            title.textContent = material.title || 'Study material';
            var description = document.createElement('p');
            description.className = 'card-text small text-muted';
            var text = String(material.description || '');
            description.textContent = text.length > 80
                ? text.slice(0, 80) + '...'
                : text;
            var footer = document.createElement('div');
            footer.className = 'mt-auto d-flex justify-content-between align-items-center';
            var views = document.createElement('small');
            views.className = 'text-muted';
            var eye = document.createElement('i');
            eye.className = 'fas fa-eye me-1';
            eye.setAttribute('aria-hidden', 'true');
            views.append(eye, document.createTextNode(
                String(Number(material.view_count) || 0) + ' views'
            ));

            var viewLink = document.createElement('a');
            var materialId = Number(material.id);
            var href = searchForm.dataset.materialUrlTemplate.replace(/0$/, String(materialId));
            var safeHref = Number.isSafeInteger(materialId) && materialId > 0
                ? safeLink(href)
                : '';
            viewLink.className = 'btn btn-primary btn-sm';
            viewLink.textContent = 'View';
            if (safeHref) viewLink.href = safeHref;
            else viewLink.removeAttribute('href');
            footer.append(views, viewLink);
            body.append(title, description, footer);
            card.append(image, body);
            column.appendChild(card);
            return column;
        }

        function showSearchEmptyState() {
            var empty = document.createElement('div');
            empty.className = 'col-12 text-center py-5';
            var icon = document.createElement('i');
            icon.className = 'fas fa-search fs-1 text-muted mb-3';
            icon.setAttribute('aria-hidden', 'true');
            var message = document.createElement('p');
            message.className = 'text-muted';
            message.textContent = 'No materials found matching your criteria.';
            empty.append(icon, message);
            resultGrid.replaceChildren(empty);
        }

        function showSearchError() {
            var error = document.createElement('div');
            error.className = 'col-12 text-center py-5';
            error.setAttribute('role', 'alert');
            var message = document.createElement('p');
            message.className = 'text-danger';
            message.textContent = 'Search results could not be loaded.';
            var retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'btn btn-outline-primary btn-sm';
            retry.textContent = 'Try again';
            retry.addEventListener('click', runSearch);
            error.append(message, retry);
            resultGrid.replaceChildren(error);
            resultStatus.textContent = 'Search results could not be loaded.';
        }

        function runSearch(event) {
            if (event) event.preventDefault();
            if (searchController) searchController.abort();
            window.clearTimeout(searchLoadingTimer);

            var formValues = new FormData(searchForm);
            var parameters = new URLSearchParams();
            formValues.forEach(function (value, key) {
                if (String(value).trim()) parameters.set(key, String(value).trim());
            });
            if (parameters.get('sort') === 'popular') {
                parameters.set('sort', 'most_viewed');
            } else if (parameters.get('sort') === 'downloads') {
                parameters.set('sort', 'most_downloaded');
            }
            parameters.set('page', '1');

            var controller = new AbortController();
            searchController = controller;
            resultStatus.textContent = 'Loading search results.';
            searchLoadingTimer = window.setTimeout(showSearchSkeletons, 120);
            var url = new URL(searchForm.dataset.apiUrl, window.location.origin);
            url.search = parameters.toString();
            fetch(url.href, {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' },
                signal: controller.signal
            }).then(function (response) {
                if (!response.ok) throw new Error('Search results unavailable');
                return response.json();
            }).then(function (data) {
                if (controller !== searchController) return;
                window.clearTimeout(searchLoadingTimer);
                var materials = Array.isArray(data.materials) ? data.materials : [];
                if (materials.length) {
                    resultGrid.replaceChildren.apply(
                        resultGrid,
                        materials.map(createMaterialCard)
                    );
                } else {
                    showSearchEmptyState();
                }
                resultGrid.setAttribute('aria-busy', 'false');
                var query = parameters.get('q');
                resultSummary.textContent = query
                    ? 'Results for "' + query + '" - ' + (Number(data.total) || 0) + ' found'
                    : 'Showing all materials - ' + (Number(data.total) || 0) + ' found';
                resultStatus.textContent = (Number(data.total) || 0) + ' search results loaded.';
                var pageUrl = new URL(searchForm.action, window.location.origin);
                pageUrl.search = parameters.toString();
                pageUrl.searchParams.delete('page');
                pageUrl.searchParams.delete('sort');
                if (formValues.get('sort')) {
                    pageUrl.searchParams.set('sort', formValues.get('sort'));
                }
                window.history.replaceState(null, '', pageUrl.pathname + pageUrl.search);
            }).catch(function (error) {
                if (error.name === 'AbortError' || controller !== searchController) return;
                window.clearTimeout(searchLoadingTimer);
                resultGrid.setAttribute('aria-busy', 'false');
                showSearchError();
            });
        }

        searchForm.addEventListener('submit', runSearch);
        window.addEventListener('popstate', function () {
            var query = new URLSearchParams(window.location.search);
            Array.prototype.forEach.call(searchForm.elements, function (field) {
                if (field.name) {
                    field.value = query.get(field.name)
                        || (field.name === 'sort' ? 'latest' : '');
                }
            });
            runSearch();
        });
    }

    var notificationWidget = document.getElementById('notificationWidget');
    if (notificationWidget) {
        var notificationList = document.getElementById('notificationList');
        var countBadge = document.getElementById('notificationCount');
        var pollTimer;
        var notificationLoadingTimer;
        var notificationRequestPending = false;
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
            notificationList.dataset.loaded = 'true';
            notificationList.setAttribute('aria-busy', 'false');
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

        function renderNotificationError() {
            notificationList.setAttribute('aria-busy', 'false');
            if (notificationList.dataset.loaded === 'true') return;
            notificationList.replaceChildren();
            var wrapper = document.createElement('div');
            wrapper.className = 'p-3';
            var message = document.createElement('p');
            message.className = 'text-danger small mb-2';
            message.textContent = 'Notifications could not be loaded.';
            var retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'btn btn-sm btn-outline-primary';
            retry.textContent = 'Try again';
            retry.addEventListener('click', refreshNotifications);
            wrapper.append(message, retry);
            notificationList.appendChild(wrapper);
        }

        function showNotificationSkeletons() {
            if (notificationList.dataset.loaded === 'true') return;
            notificationList.replaceChildren();
            for (var index = 0; index < 2; index += 1) {
                var row = document.createElement('div');
                row.className = 'studyhub-skeleton-notification';
                row.setAttribute('aria-hidden', 'true');
                var title = document.createElement('span');
                title.className = 'studyhub-skeleton studyhub-skeleton-line';
                var message = document.createElement('span');
                message.className = 'studyhub-skeleton studyhub-skeleton-line';
                row.append(title, message);
                notificationList.appendChild(row);
            }
        }

        function refreshNotifications() {
            if (notificationRequestPending) return;
            notificationRequestPending = true;
            if (notificationList.dataset.loaded !== 'true') {
                notificationList.setAttribute('aria-busy', 'true');
                notificationLoadingTimer = window.setTimeout(
                    showNotificationSkeletons,
                    120
                );
            }
            fetch(notificationWidget.dataset.notificationsUrl, {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' }
            }).then(function (response) {
                if (!response.ok) throw new Error('Notifications unavailable');
                return response.json();
            }).then(function (data) {
                window.clearTimeout(notificationLoadingTimer);
                renderNotifications(data.notifications || []);
                updateCount(data.unread_count);
                var status = document.getElementById('notificationStatus');
                if (status) status.textContent = '';
            }).catch(function () {
                window.clearTimeout(notificationLoadingTimer);
                var status = document.getElementById('notificationStatus');
                if (status) status.textContent = 'Notifications could not be loaded. Please try again.';
                renderNotificationError();
            }).then(function () {
                notificationRequestPending = false;
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
