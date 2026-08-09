// Service Worker mínimo — existe para o site ser reconhecido como PWA instalável
// (checagem do Chrome/Lighthouse e do Bubblewrap ao gerar o pacote Android/TWA).
// Não faz cache agressivo: cada rota é dinâmica (dados de plantio, sessão de login),
// então por enquanto só guardamos a shell da home para abrir algo offline.
const CACHE = 'plantando-vida-shell-v1';
const SHELL_URL = '/';

self.addEventListener('install', (evento) => {
  self.skipWaiting();
  evento.waitUntil(
    caches.open(CACHE).then((cache) => cache.add(SHELL_URL))
  );
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    caches.keys().then((chaves) =>
      Promise.all(chaves.filter((chave) => chave !== CACHE).map((chave) => caches.delete(chave)))
    )
  );
  self.clients.claim();
});

// Network-first: sempre busca a versão atual da rede; só cai no cache da home
// (SHELL_URL) se a rede falhar (offline), evitando servir páginas dinâmicas velhas.
self.addEventListener('fetch', (evento) => {
  if (evento.request.method !== 'GET') return;

  evento.respondWith(
    fetch(evento.request).catch(() =>
      caches.match(evento.request).then((resposta) => resposta || caches.match(SHELL_URL))
    )
  );
});
