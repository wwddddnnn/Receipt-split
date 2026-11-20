<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <main class="shell">
    <header class="topbar">
      <RouterLink class="brand" to="/">一起分账</RouterLink>
      <RouterLink v-if="route.path === '/login'" class="text-link" to="/">记一笔</RouterLink>
      <RouterLink v-else-if="route.path !== '/settlement'" class="text-link" to="/settlement">月结</RouterLink>
      <RouterLink v-else class="text-link" to="/">记一笔</RouterLink>
    </header>
    <RouterView />
  </main>
</template>
