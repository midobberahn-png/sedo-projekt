/* Print the complete document, never the application shell or review form. */
(() => {
  'use strict';
  let previousFrame = null;
  let previousURL = null;
  const clearFrame = () => {
    if (previousFrame) previousFrame.remove();
    if (previousURL) URL.revokeObjectURL(previousURL);
    previousFrame = previousURL = null;
  };
  const encoded = bytes => {
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 8192) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
    }
    return btoa(binary);
  };
  const browserPrint = blob => new Promise((resolve, reject) => {
    clearFrame();
    const frame = document.createElement('iframe');
    frame.title = 'Eingangsrechnung drucken';
    frame.setAttribute('aria-hidden', 'true');
    frame.style.cssText = 'position:fixed;left:-10000px;top:0;width:900px;height:1200px;border:0';
    previousFrame = frame;
    previousURL = URL.createObjectURL(blob);
    frame.onload = () => {
      try {
        frame.contentWindow.focus();
        frame.contentWindow.print();
        resolve();
      } catch (_) {
        reject(new Error('Der Druckdialog konnte nicht geöffnet werden. Bitte den Originalbeleg herunterladen und drucken.'));
      }
    };
    frame.onerror = () => reject(new Error('Der Druckbeleg konnte nicht geladen werden.'));
    frame.src = previousURL;
    document.body.append(frame);
  });
  document.querySelectorAll('[data-incoming-print]').forEach(link => {
    link.addEventListener('click', async event => {
      event.preventDefault();
      if (link.getAttribute('aria-disabled') === 'true') return;
      link.setAttribute('aria-disabled', 'true');
      const oldError = document.getElementById('incoming-print-error');
      if (oldError) oldError.remove();
      try {
        const response = await fetch(link.href, {credentials: 'same-origin', headers: {'Accept': 'application/pdf'}});
        if (!response.ok || !response.headers.get('content-type')?.startsWith('application/pdf')) {
          throw new Error(response.status === 409 ? await response.text() : 'Der Druckbeleg konnte nicht geladen werden. Bitte den Beleg erneut öffnen.');
        }
        const blob = await response.blob();
        if (blob.size > 32 * 1024 * 1024) throw new Error('Der Beleg ist zum direkten Drucken zu groß.');
        const api = window.pywebview?.api;
        let native = false;
        if (api?.print_incoming_pdf) {
          const result = await api.print_incoming_pdf(encoded(new Uint8Array(await blob.arrayBuffer())));
          native = result.native;
          if (native && !result.ok) throw new Error(result.error);
        }
        if (!native) await browserPrint(blob);
      } catch (error) {
        const notice = document.createElement('p');
        notice.id = 'incoming-print-error';
        notice.className = 'notice error';
        notice.setAttribute('role', 'alert');
        notice.textContent = error.message || 'Der Druckdialog konnte nicht geöffnet werden.';
        document.getElementById('main-content').prepend(notice);
      } finally {
        link.removeAttribute('aria-disabled');
      }
    });
  });
  window.addEventListener('beforeunload', clearFrame);
})();
