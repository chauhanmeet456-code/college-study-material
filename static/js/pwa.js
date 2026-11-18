'use strict';

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
    .catch(error => {
      console.error('StudyHub offline support could not be registered.', error);
    });
}
