<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { getReceipts, money, type ReceiptList } from '@/api'

const month = ref('')
const status = ref<'' | 'confirmed' | 'pending'>('')
const result = ref<ReceiptList>()
const loading = ref(false)
const error = ref('')

async function load(page = 1) {
  loading.value = true; error.value = ''
  try { result.value = await getReceipts({ month: month.value || undefined, status: status.value || undefined, page }) }
  catch (caught) { error.value = caught instanceof Error ? caught.message : '无法载入账单。' }
  finally { loading.value = false }
}
watch([month, status], () => load())
onMounted(load)
</script>

<template>
  <section class="page receipt-list-page">
    <div class="eyebrow">账单</div><h1>你的账单记录。</h1>
    <div class="list-filters">
      <label><span>月份</span><input v-model="month" type="month" /></label>
      <label><span>状态</span><select v-model="status"><option value="">全部状态</option><option value="confirmed">已入账</option><option value="pending">待确认</option></select></label>
    </div>
    <p v-if="error" class="alert">{{ error }}</p>
    <p v-else-if="loading && !result" class="loading">正在载入账单…</p>
    <template v-else-if="result">
      <p v-if="!result.items.length" class="empty-copy">没有符合筛选条件的账单。</p>
      <div v-else class="receipt-list-cards">
        <RouterLink v-for="item in result.items" :key="item.receipt_id" class="receipt-list-card" :to="`/receipt/${item.receipt_id}`">
          <div><span class="status-pill" :class="item.status">{{ item.status === 'pending' ? '待确认' : '已入账' }}</span><span class="list-date">{{ item.receipt_date ?? '日期待确认' }}</span></div>
          <strong>{{ money(item.total_cents) }}</strong>
          <p>{{ item.payer_display_name ?? '垫付人待确认' }} 垫付 · {{ item.note_summary || '无备注' }}</p>
          <small v-if="item.status === 'pending'">{{ item.clarification_count ? `${item.clarification_count} 项待确认` : '待补充分账信息' }}</small>
        </RouterLink>
      </div>
      <table v-if="result.items.length" class="receipt-table">
        <thead><tr><th>日期</th><th>状态</th><th>垫付人</th><th>备注</th><th>金额</th></tr></thead>
        <tbody><tr v-for="item in result.items" :key="item.receipt_id" tabindex="0" @click="$router.push(`/receipt/${item.receipt_id}`)" @keydown.enter="$router.push(`/receipt/${item.receipt_id}`)">
          <td>{{ item.receipt_date ?? '待确认' }}</td><td><span class="status-pill" :class="item.status">{{ item.status === 'pending' ? '待确认' : '已入账' }}</span></td><td>{{ item.payer_display_name ?? '待确认' }}</td><td>{{ item.note_summary || '无备注' }}</td><td>{{ money(item.total_cents) }}</td>
        </tr></tbody>
      </table>
      <nav v-if="result.total > result.page_size" class="pagination" aria-label="账单分页">
        <button class="secondary" :disabled="loading || result.page === 1" @click="load(result.page - 1)">上一页</button>
        <span>第 {{ result.page }} 页 / 共 {{ Math.ceil(result.total / result.page_size) }} 页</span>
        <button class="secondary" :disabled="loading || result.page * result.page_size >= result.total" @click="load(result.page + 1)">下一页</button>
      </nav>
    </template>
  </section>
</template>
