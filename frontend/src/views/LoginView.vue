<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login, type ApiError } from '@/api'

const router = useRouter()
const route = useRoute()
const memberId = ref('')
const password = ref('')
const error = ref('')
const submitting = ref(false)

async function submit() {
  if (!memberId.value.trim() || !password.value || submitting.value) return
  error.value = ''
  submitting.value = true
  try {
    await login(memberId.value.trim(), password.value)
    const next = typeof route.query.next === 'string' && route.query.next.startsWith('/')
      ? route.query.next : '/'
    await router.replace(next)
  } catch (caught) {
    const apiError = caught as ApiError
    error.value = apiError.message
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="page login-page">
    <div class="eyebrow">一起分账</div>
    <h1>登录后查看你的账单。</h1>
    <p class="intro">账号由维护者创建；如需重置密码，请联系维护者。</p>
    <form class="form" @submit.prevent="submit">
      <label>
        <span>账号</span>
        <input v-model="memberId" autocomplete="username" :disabled="submitting" required />
      </label>
      <label>
        <span>密码</span>
        <input v-model="password" type="password" autocomplete="current-password" :disabled="submitting" required />
      </label>
      <p v-if="error" class="alert">{{ error }}</p>
      <button class="primary" type="submit" :disabled="submitting">
        {{ submitting ? '正在登录…' : '登录' }}
      </button>
    </form>
  </section>
</template>
