<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { getMembers, getReceipt, money, type Receipt } from '@/api'

const route = useRoute()
const receipt = ref<Receipt>()
const error = ref('')
const enlargedPhoto = ref<string>()
const displayNames = ref<Record<string, string>>({})

onMounted(async () => {
  try {
    receipt.value = await getReceipt(String(route.params.id))
    try {
      displayNames.value = Object.fromEntries((await getMembers()).members.map((member) => [member.member_id, member.display_name]))
    } catch { /* Receipt data remains useful while the member list is unavailable. */ }
  } catch {
    error.value = '找不到这笔记录，或服务暂时不可用。'
  }
})

function nameFor(memberId: string | null) {
  if (!memberId) return '未记录'
  return displayNames.value[memberId] ?? memberId
}

function timestamp(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}
</script>

<template>
  <section class="page receipt-detail-page">
    <template v-if="receipt">
      <div class="receipt-hero">
        <div class="status-line">
          <span class="status-pill" :class="receipt.status">{{ receipt.status === 'confirmed' ? '已入账' : '待确认' }}</span>
          <span>账单 #{{ receipt.receipt_id }}</span>
        </div>
        <div class="hero-amount">{{ money(receipt.total_cents) }}</div>
        <p class="receipt-date">{{ receipt.receipt_date ?? '日期尚未确认' }}</p>
      </div>

      <div class="detail-grid">
        <section class="card detail-media">
          <div class="section-heading"><h2>收据照片</h2><span v-if="receipt.photo_urls.length" class="section-note">点击放大</span></div>
          <div v-if="receipt.photo_urls.length" class="receipt-photos">
            <button v-for="(url, index) in receipt.photo_urls" :key="url" class="photo-button" type="button" :aria-label="`放大查看收据原图 ${index + 1}`" @click="enlargedPhoto = url"><img :src="url" :alt="`收据原图 ${index + 1}`" /></button>
          </div>
          <p v-else class="empty-copy">这笔历史账单没有保存收据图片。</p>
        </section>

        <div class="detail-information">
          <section class="card people-card">
            <div class="section-heading"><h2>谁垫付</h2><span class="person-tag">{{ nameFor(receipt.payer_id) }}</span></div>
            <div class="section-heading"><h2>谁承担</h2><span v-if="receipt.status === 'pending'" class="section-note">确认后计算</span></div>
            <dl v-if="Object.keys(receipt.shares).length" class="amount-list">
              <template v-for="(amount, member) in receipt.shares" :key="member"><dt>{{ nameFor(member) }}</dt><dd>{{ money(amount) }}</dd></template>
            </dl>
            <p v-else class="empty-copy">尚未生成承担金额。补齐规则并确认后会显示在这里。</p>
          </section>

          <section class="card muted-card"><h2>备注</h2><p>{{ receipt.note || '未保存备注。' }}</p></section>
          <section v-if="receipt.rule_editable" class="card muted-card">
            <h2>当前分账规则</h2>
            <p v-if="receipt.individual_charges.length">单独承担：{{ receipt.individual_charges.map((item) => `${nameFor(item.member_id)} ${money(item.amount_cents)}`).join('、') }}</p>
            <p v-if="receipt.remainder_member_ids.length">余款平摊：{{ receipt.remainder_member_ids.map(nameFor).join('、') }}</p>
          </section>
          <section v-else-if="receipt.status === 'confirmed'" class="card muted-card"><h2>历史分账</h2><p>仅保存了最终承担金额，原始规则无法还原。编辑时可选择改用规则分账。</p></section>

          <section v-if="receipt.status === 'pending' && receipt.needs_clarification.length" class="clarification-card">
            <h2>待确认项</h2><ul><li v-for="item in receipt.needs_clarification" :key="`${item.field}-${item.reason}`"><b>{{ item.field }}</b><span>{{ item.reason }}</span></li></ul>
          </section>
          <section v-if="receipt.updated_at" class="audit-line">最后由 {{ nameFor(receipt.updated_by ?? receipt.created_by) }} 修改于 {{ timestamp(receipt.updated_at) }}</section>
        </div>
      </div>

      <div class="detail-actions">
        <RouterLink class="primary block-link" :to="`/receipt/${receipt.receipt_id}/edit`">{{ receipt.status === 'pending' ? '继续编辑并确认' : '编辑账单' }}</RouterLink>
        <RouterLink class="secondary block-link" to="/settlement">查看月结</RouterLink>
      </div>
    </template>
    <template v-else-if="error"><p class="alert">{{ error }}</p><RouterLink class="primary block-link" to="/">回到上传页</RouterLink></template>
    <p v-else class="loading">正在载入账单…</p>
    <div v-if="enlargedPhoto" class="photo-modal" role="dialog" aria-modal="true" aria-label="收据原图" @click.self="enlargedPhoto = undefined"><button type="button" class="photo-close" @click="enlargedPhoto = undefined">关闭图片</button><img :src="enlargedPhoto" alt="收据原图放大查看" /></div>
  </section>
</template>
