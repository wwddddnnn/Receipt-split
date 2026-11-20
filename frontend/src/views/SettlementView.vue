<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getSettlement, money, type Settlement } from '@/api'

const month = ref(new Date().toISOString().slice(0, 7))
const strategy = ref<Settlement['strategy']>('group_netting')
const settlement = ref<Settlement>()
const error = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    settlement.value = await getSettlement(month.value, strategy.value)
  } catch {
    settlement.value = undefined
    error.value = '无法载入这个月的月结。请选择有效月份后重试。'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="page">
    <div class="eyebrow">月结</div>
    <h1>谁该收，谁该付。</h1>
    <form class="month-picker" @submit.prevent="load">
      <input v-model="month" type="month" required aria-label="结算月份" />
      <button class="secondary" :disabled="loading">{{ loading ? '载入中…' : '查看' }}</button>
    </form>
    <fieldset class="strategy-picker" :disabled="loading">
      <legend>转账方式</legend>
      <label>
        <input v-model="strategy" type="radio" value="group_netting" @change="load" />
        <span><b>全组净额</b><small>抵销所有人的收支，通常转账更少。</small></span>
      </label>
      <label>
        <input v-model="strategy" type="radio" value="receipt_links" @change="load" />
        <span><b>按收据关系</b><small>只在同一对成员间抵销，保留谁欠谁。</small></span>
      </label>
    </fieldset>
    <p v-if="error" class="alert">{{ error }}</p>
    <template v-else-if="settlement">
      <section class="card">
        <h2>相关成员的净余额</h2>
        <p class="hint">本月转账关系树中的成员。正数为应收，负数为应付。</p>
        <dl class="amount-list">
          <template v-for="balance in settlement.balances" :key="balance.member_id">
            <dt>{{ balance.display_name }}</dt>
            <dd :class="{ positive: balance.balance_cents > 0, negative: balance.balance_cents < 0 }">
              {{ balance.balance_cents > 0 ? '+' : '' }}{{ money(balance.balance_cents) }}
            </dd>
          </template>
        </dl>
      </section>
      <section class="card">
        <h2>转账建议</h2>
        <ul v-if="settlement.transfers.length" class="transfer-list">
          <li v-for="transfer in settlement.transfers" :key="`${transfer.direction}-${transfer.counterparty_member_id}`">
            <span v-if="transfer.direction === 'pay'">{{ settlement.my_display_name }} <b>转给</b> {{ transfer.counterparty_display_name }}</span>
            <span v-else>{{ transfer.counterparty_display_name }} <b>转给</b> {{ settlement.my_display_name }}</span>
            <strong>{{ money(transfer.amount_cents) }}</strong>
          </li>
        </ul>
        <p v-else class="hint">这个月暂时没有需要结算的款项。</p>
      </section>
    </template>
    <p v-else class="loading">正在载入月结…</p>
  </section>
</template>
