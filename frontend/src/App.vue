<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <main class="shell">
    <header class="topbar">
      <RouterLink class="brand" to="/">一起分账</RouterLink>
      <nav v-if="route.path !== '/login'" class="topnav"><RouterLink class="text-link" to="/receipts">账单</RouterLink><RouterLink class="text-link" to="/settlement">月结</RouterLink><RouterLink class="text-link" to="/">快速记账</RouterLink></nav>
      <RouterLink v-else class="text-link" to="/">快速记账</RouterLink>
    </header>
    <RouterView />
  </main>
</template>
