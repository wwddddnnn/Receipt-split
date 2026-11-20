<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { getReceipt, money, type Receipt } from '@/api'

const route = useRoute()
const receipt = ref<Receipt>()
const error = ref('')

onMounted(async () => {
  try {
    receipt.value = await getReceipt(String(route.params.id))
  } catch {
    error.value = '找不到这笔记录，或服务暂时不可用。'
  }
})
</script>

<template>
  <section class="page">
    <template v-if="receipt">
      <div class="eyebrow success">已入账</div>
      <h1>{{ money(receipt.total_cents) }} 已记入账本</h1>
      <p class="intro">{{ receipt.receipt_date ?? '日期未识别' }} · {{ receipt.payer_id ?? '垫付人未识别' }} 垫付</p>

      <div class="receipt-photos">
        <img v-for="(url, index) in receipt.photo_urls" :key="url" :src="url" :alt="`收据原图 ${index + 1}`" />
      </div>

      <section class="card">
        <h2>本笔承担</h2>
        <dl class="amount-list">
          <template v-for="(amount, member) in receipt.shares" :key="member">
            <dt>{{ member }}</dt><dd>{{ money(amount) }}</dd>
          </template>
        </dl>
      </section>

      <section class="card muted-card">
        <h2>备注</h2>
        <p>{{ receipt.note }}</p>
      </section>
      <RouterLink class="primary block-link" to="/settlement">查看本月月结</RouterLink>
    </template>
    <template v-else-if="error">
      <p class="alert">{{ error }}</p>
      <RouterLink class="primary block-link" to="/">回到上传页</RouterLink>
    </template>
    <p v-else class="loading">正在载入入账结果…</p>
  </section>
</template>
