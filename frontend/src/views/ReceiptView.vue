<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { getReceipt, money, type Receipt } from '@/api'

const route = useRoute()
const receipt = ref<Receipt>()
const error = ref('')
const enlargedPhoto = ref<string>()

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
      <div class="eyebrow" :class="{ success: receipt.status === 'confirmed' }">{{ receipt.status === 'confirmed' ? '已入账' : '待确认' }}</div>
      <h1>{{ receipt.status === 'confirmed' ? `${money(receipt.total_cents)} 已记入账本` : '补齐信息后确认入账。' }}</h1>
      <p class="intro">{{ receipt.receipt_date ?? '日期未识别' }} · {{ receipt.payer_id ?? '垫付人未识别' }} 垫付</p>

      <div v-if="receipt.photo_urls.length" class="receipt-photos">
        <button v-for="(url, index) in receipt.photo_urls" :key="url" class="photo-button" type="button" @click="enlargedPhoto = url">
          <img :src="url" :alt="`放大查看收据原图 ${index + 1}`" />
        </button>
      </div>

      <section class="card">
        <h2>谁垫付</h2>
        <p>{{ receipt.payer_id ?? '历史记录未保存垫付人' }}</p>
      </section>
      <section class="card">
        <h2>谁承担</h2>
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
      <section v-if="receipt.status === 'pending' && receipt.needs_clarification.length" class="card">
        <h2>待确认原因</h2>
        <ul><li v-for="item in receipt.needs_clarification" :key="`${item.field}-${item.reason}`">{{ item.field }}：{{ item.reason }}</li></ul>
      </section>
      <section v-if="receipt.updated_at" class="card muted-card">
        <h2>修改记录</h2>
        <p>{{ receipt.updated_by ?? receipt.created_by ?? '历史记录' }} · {{ new Date(receipt.updated_at).toLocaleString('en-CA') }}</p>
      </section>
      <RouterLink class="secondary block-link" :to="`/receipt/${receipt.receipt_id}/edit`">编辑账单</RouterLink>
      <RouterLink class="primary block-link" to="/settlement">查看本月月结</RouterLink>
    </template>
    <template v-else-if="error">
      <p class="alert">{{ error }}</p>
      <RouterLink class="primary block-link" to="/">回到上传页</RouterLink>
    </template>
    <p v-else class="loading">正在载入入账结果…</p>
    <div v-if="enlargedPhoto" class="photo-modal" role="dialog" aria-modal="true" @click.self="enlargedPhoto = undefined">
      <button type="button" class="photo-close" @click="enlargedPhoto = undefined">关闭</button>
      <img :src="enlargedPhoto" alt="收据原图放大查看" />
    </div>
  </section>
</template>
