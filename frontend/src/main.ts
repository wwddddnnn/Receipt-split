import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import { getMe } from './api'
import './style.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: () => import('./views/LoginView.vue') },
    { path: '/', component: () => import('./views/UploadView.vue') },
    { path: '/receipt/:id', component: () => import('./views/ReceiptView.vue') },
    { path: '/settlement', component: () => import('./views/SettlementView.vue') },
  ],
})

router.beforeEach(async (to) => {
  if (to.path === '/login') return true
  try {
    await getMe()
    return true
  } catch {
    return { path: '/login', query: { next: to.fullPath } }
  }
})

createApp(App).use(router).mount('#app')
