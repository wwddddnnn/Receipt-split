<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { getMembers, getReceipt, money, previewReceipt, saveReceiptDraft, updateReceipt, type ApiError, type Member, type Receipt, type ReceiptEdit } from '@/api'

const route = useRoute()
const router = useRouter()
const members = ref<Member[]>([])
const loading = ref(true)
const saving = ref(false)
const dirty = ref(false)
const error = ref('')
const preview = ref<Record<string, number>>({})
const previewing = ref(false)
const previewIsCurrent = ref(true)
const ruleMode = ref(true)
const isPending = ref(false)
const clarifications = ref<{ field: string; reason: string }[]>([])
const sourceReceipt = ref<Receipt>()
const form = ref<ReceiptEdit>({ version: 1, receipt_date: '', total_cents: null, subtotal_cents: null, payer_id: '', note: '', individual_charges: [], remainder_member_ids: [] })
const receiptId = String(route.params.id)

const totalDollars = computed({
  get: () => form.value.total_cents == null ? '' : (form.value.total_cents / 100).toFixed(2),
  set: (value: string) => { form.value.total_cents = value === '' ? null : Math.round(Number(value) * 100) || null; markDirty() },
})
const subtotalDollars = computed({
  get: () => form.value.subtotal_cents == null ? '' : (form.value.subtotal_cents / 100).toFixed(2),
  set: (value: string) => { form.value.subtotal_cents = value === '' ? null : Math.round(Number(value) * 100) || null; markDirty() },
})
const assignedCents = computed(() => form.value.individual_charges.reduce((sum, item) => sum + (item.amount_cents || 0), 0))
const splitBaseCents = computed(() => form.value.subtotal_cents ?? form.value.total_cents)
const splitBaseLabel = computed(() => form.value.subtotal_cents == null ? '实付总额' : '税前小计')
const remainderCents = computed(() => splitBaseCents.value == null ? null : splitBaseCents.value - assignedCents.value)
const title = computed(() => isPending.value ? '核对草稿，确认再入账。' : '修改这笔已入账账单。')

function markDirty() { dirty.value = true; previewIsCurrent.value = false }
function payload(): ReceiptEdit {
  return {
    ...form.value,
    receipt_date: form.value.receipt_date || null,
    payer_id: form.value.payer_id || null,
    individual_charges: form.value.individual_charges.filter((item) => item.member_id && item.amount_cents > 0),
    remainder_member_ids: [...form.value.remainder_member_ids],
  }
}
function cents(value: string) { return Math.round(Number(value) * 100) || 0 }
function setCharge(index: number, value: string) { form.value.individual_charges[index].amount_cents = cents(value); markDirty() }
function toggleRemainder(memberId: string) {
  const values = form.value.remainder_member_ids
  const index = values.indexOf(memberId)
  if (index >= 0) values.splice(index, 1)
  else values.push(memberId)
  markDirty()
}
function addCharge() { form.value.individual_charges.push({ member_id: members.value[0]?.member_id ?? '', amount_cents: 0 }); markDirty() }
function removeCharge(index: number) { form.value.individual_charges.splice(index, 1); markDirty() }

async function refreshPreview() {
  error.value = ''; previewing.value = true
  try {
    preview.value = await previewReceipt(receiptId, payload())
    previewIsCurrent.value = true
  } catch (caught) {
    error.value = (caught as ApiError).message
  } finally {
    previewing.value = false
  }
}

async function save(confirm: boolean) {
  saving.value = true; error.value = ''
  try {
    const saved = confirm ? await updateReceipt(receiptId, payload()) : await saveReceiptDraft(receiptId, payload())
    dirty.value = false
    await router.replace(`/receipt/${saved.receipt_id}`)
  } catch (caught) {
    const apiError = caught as ApiError
    error.value = apiError.code === 'receipt_update_conflict'
      ? '这笔账单已被其他人修改。你的输入仍保留；请重新载入后再决定如何合并。'
      : apiError.message
  } finally {
    saving.value = false
  }
}
function cancel() { router.push(`/receipt/${receiptId}`) }

onMounted(async () => {
  try {
    const [receipt, available] = await Promise.all([getReceipt(receiptId), getMembers()])
    sourceReceipt.value = receipt
    members.value = available.members
    form.value = {
      version: receipt.version,
      receipt_date: receipt.receipt_date ?? '',
      total_cents: receipt.total_cents,
      subtotal_cents: receipt.subtotal_cents,
      payer_id: receipt.payer_id ?? '',
      note: receipt.note,
      individual_charges: receipt.individual_charges,
      remainder_member_ids: receipt.remainder_member_ids,
    }
    ruleMode.value = receipt.rule_editable
    isPending.value = receipt.status === 'pending'
    clarifications.value = receipt.needs_clarification
    preview.value = receipt.shares
  } catch (caught) {
    error.value = (caught as ApiError).message
  } finally {
    loading.value = false
  }
})

onBeforeRouteLeave(() => !dirty.value || window.confirm('有未保存的修改，确定离开吗？'))
function warnBeforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(() => window.addEventListener('beforeunload', warnBeforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', warnBeforeUnload))
</script>

<template>
  <section class="page receipt-editor-page">
    <div class="status-line"><span class="status-pill" :class="isPending ? 'pending' : 'confirmed'">{{ isPending ? '待确认草稿' : '已入账账单' }}</span><span v-if="dirty" class="unsaved-mark">有未保存修改</span></div>
    <div class="eyebrow">{{ isPending ? '草稿确认' : '账单修改' }}</div><h1>{{ title }}</h1>
    <p class="intro">{{ isPending ? '草稿不会参与月结；确认前需要补齐实付金额、垫付人和分账规则。日期可留空，税前小计未填写时会按实付总额分账。' : '保存后会重新计算承担金额，并实时更新相关月份的月结。' }}</p>
    <p v-if="loading" class="loading">正在载入账单…</p>
    <div v-else class="editor-layout">
      <aside class="editor-reference" aria-label="账单参考信息">
        <div class="section-heading"><h2>收据参考</h2><span v-if="sourceReceipt?.photo_urls.length" class="section-note">原图</span></div>
        <div v-if="sourceReceipt?.photo_urls.length" class="editor-photo-strip">
          <img v-for="(url, index) in sourceReceipt.photo_urls" :key="url" :src="url" :alt="`收据原图 ${index + 1}`" />
        </div>
        <p v-else class="empty-copy">这笔账单没有可供核对的收据图片。</p>
      </aside>
      <form class="form editor-form" @submit.prevent="save(true)">
      <section v-if="isPending && clarifications.length" class="clarification-card">
        <h2>仍待确认</h2><ul><li v-for="item in clarifications" :key="`${item.field}-${item.reason}`"><b>{{ item.field }}</b><span>{{ item.reason }}</span></li></ul>
      </section>

      <section class="editor-section">
        <div class="section-heading"><h2>账单信息</h2><span class="section-note">金额以 CAD 计</span></div>
        <div class="input-grid">
          <label><span>日期</span><input v-model="form.receipt_date" type="date" @change="markDirty" /><small>留空默认使用该账单入账日期</small></label>
          <label><span>实付总额</span><div class="money-input"><span>CAD</span><input v-model="totalDollars" type="number" min="0.01" step="0.01" inputmode="decimal" aria-label="实付总额（CAD）" /></div></label>
          <label><span>税前小计（可选）</span><div class="money-input"><span>CAD</span><input v-model="subtotalDollars" type="number" min="0.01" step="0.01" inputmode="decimal" aria-label="税前小计（CAD，可选）" /></div><small>填写时不含税、小费和服务费；留空则按实付总额分账。</small></label>
        </div>
        <label><span>垫付人</span><select v-model="form.payer_id" @change="markDirty"><option value="">未选择</option><option v-for="member in members" :key="member.member_id" :value="member.member_id">{{ member.display_name }}</option></select></label>
        <label><span>备注</span><textarea v-model="form.note" rows="3" placeholder="例如：午餐，我垫付，三人平分" @input="markDirty" /></label>
      </section>

      <section class="editor-section rule-editor">
        <div class="section-heading"><h2>分账规则</h2><span v-if="ruleMode" class="section-note">由后端重新计算</span></div>
        <p v-if="!ruleMode" class="hint">这笔历史账单只有最终承担金额，无法还原原规则。选择“改用规则分账”后才会覆盖它。</p>
        <button v-if="!ruleMode" type="button" class="secondary compact-button" @click="ruleMode = true; markDirty()">改用规则分账</button>
        <template v-else>
          <div class="rule-header"><h3>单独承担项</h3><span>{{ money(assignedCents) }}</span></div>
          <p class="hint">分账基数：{{ splitBaseCents == null ? '待填写实付总额' : `${splitBaseLabel} ${money(splitBaseCents)}` }}</p>
          <div v-for="(charge, index) in form.individual_charges" :key="index" class="charge-row">
            <select v-model="charge.member_id" @change="markDirty"><option v-for="member in members" :key="member.member_id" :value="member.member_id">{{ member.display_name }}</option></select>
            <input :value="(charge.amount_cents / 100).toFixed(2)" type="number" min="0.01" step="0.01" inputmode="decimal" aria-label="单独承担金额" @input="setCharge(index, ($event.target as HTMLInputElement).value)" />
            <button type="button" class="text-link" @click="removeCharge(index)">移除</button>
          </div>
          <button type="button" class="secondary compact-button" @click="addCharge">添加单独承担项</button>
          <div class="rule-header remainder-header"><h3>余款平摊成员</h3><span>{{ remainderCents == null ? '待填写实付总额' : money(remainderCents) }}</span></div>
          <div class="member-checks"><label v-for="member in members" :key="member.member_id" class="check-row"><input type="checkbox" :checked="form.remainder_member_ids.includes(member.member_id)" @change="toggleRemainder(member.member_id)" /><span>{{ member.display_name }}</span></label></div>
        </template>
      </section>

      <p v-if="error" class="alert">{{ error }}</p>
      <section class="editor-section preview-card">
        <div class="section-heading"><h2>每人承担金额</h2><span class="section-note" :class="{ 'preview-stale': !previewIsCurrent }">{{ previewIsCurrent ? '已按当前规则计算' : '规则已改动，请重新预览' }}</span></div>
        <dl v-if="Object.keys(preview).length" class="amount-list"><template v-for="(amount, member) in preview" :key="member"><dt>{{ members.find((item) => item.member_id === member)?.display_name ?? member }}</dt><dd>{{ money(amount) }}</dd></template></dl>
        <p v-else class="empty-copy">填写完整分账规则后，可预览每人的承担金额。</p>
        <button type="button" class="secondary preview-button" :disabled="previewing" @click="refreshPreview">{{ previewing ? '正在计算…' : '预览计算结果' }}</button>
      </section>

      <div class="editor-actions">
        <button v-if="isPending" type="button" class="secondary" :disabled="saving" @click="save(false)">{{ saving ? '正在保存…' : '保存草稿' }}</button>
        <button class="primary" :disabled="saving">{{ saving ? (isPending ? '正在确认…' : '正在保存…') : (isPending ? '确认入账' : '保存修改') }}</button>
        <button type="button" class="cancel-button" @click="cancel">取消编辑</button>
      </div>
      </form>
    </div>
  </section>
</template>
