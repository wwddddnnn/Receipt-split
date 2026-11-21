<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { getMembers, getReceipt, money, previewReceipt, updateReceipt, type ApiError, type Member, type ReceiptEdit } from '@/api'

const route = useRoute()
const router = useRouter()
const members = ref<Member[]>([])
const loading = ref(true)
const saving = ref(false)
const dirty = ref(false)
const error = ref('')
const preview = ref<Record<string, number>>({})
const ruleMode = ref(true)
const form = ref<ReceiptEdit>({ version: 1, receipt_date: '', total_cents: 0, payer_id: '', note: '', individual_charges: [], remainder_member_ids: [] })
const receiptId = String(route.params.id)
const totalDollars = computed({ get: () => (form.value.total_cents / 100).toFixed(2), set: (value: string) => { form.value.total_cents = Math.round(Number(value) * 100) || 0; dirty.value = true } })

function markDirty() { dirty.value = true }
function payload(): ReceiptEdit { return { ...form.value, individual_charges: form.value.individual_charges.filter((item) => item.member_id && item.amount_cents > 0), remainder_member_ids: [...form.value.remainder_member_ids] } }
function cents(value: string) { return Math.round(Number(value) * 100) || 0 }
function setCharge(index: number, value: string) { form.value.individual_charges[index].amount_cents = cents(value); markDirty() }
function setChargeEvent(index: number, event: Event) { setCharge(index, (event.target as HTMLInputElement).value) }
function toggleRemainder(memberId: string) { const values = form.value.remainder_member_ids; const index = values.indexOf(memberId); if (index >= 0) values.splice(index, 1); else values.push(memberId); markDirty() }
function addCharge() { form.value.individual_charges.push({ member_id: members.value[0]?.member_id ?? '', amount_cents: 0 }); markDirty() }
function removeCharge(index: number) { form.value.individual_charges.splice(index, 1); markDirty() }

async function refreshPreview() {
  error.value = ''
  try { preview.value = await previewReceipt(receiptId, payload()) }
  catch (caught) { error.value = (caught as ApiError).message }
}
async function save() {
  saving.value = true; error.value = ''
  try {
    const saved = await updateReceipt(receiptId, payload())
    dirty.value = false
    await router.replace(`/receipt/${saved.receipt_id}`)
  } catch (caught) {
    const apiError = caught as ApiError
    error.value = apiError.code === 'receipt_update_conflict' ? '这笔账单已被其他人修改。你的输入仍保留；请重新载入后再决定如何合并。' : apiError.message
  } finally { saving.value = false }
}
function cancel() { router.push(`/receipt/${receiptId}`) }

onMounted(async () => {
  try {
    const [receipt, available] = await Promise.all([getReceipt(receiptId), getMembers()])
    members.value = available.members
    form.value = { version: receipt.version, receipt_date: receipt.receipt_date ?? '', total_cents: receipt.total_cents ?? 0, payer_id: receipt.payer_id ?? '', note: receipt.note, individual_charges: receipt.individual_charges, remainder_member_ids: receipt.remainder_member_ids }
    ruleMode.value = receipt.rule_editable
    preview.value = receipt.shares
  } catch (caught) { error.value = (caught as ApiError).message }
  finally { loading.value = false }
})

onBeforeRouteLeave(() => !dirty.value || window.confirm('有未保存的修改，确定离开吗？'))
</script>

<template>
  <section class="page">
    <div class="eyebrow">编辑账单</div><h1>修改分账规则。</h1>
    <p v-if="loading" class="loading">正在载入账单…</p>
    <form v-else class="form" @submit.prevent="save">
      <label><span>日期</span><input v-model="form.receipt_date" type="date" required @change="markDirty" /></label>
      <label><span>实付总额（CAD）</span><input v-model="totalDollars" type="number" min="0.01" step="0.01" required /></label>
      <label><span>垫付人</span><select v-model="form.payer_id" required @change="markDirty"><option v-for="member in members" :key="member.member_id" :value="member.member_id">{{ member.display_name }}</option></select></label>
      <label><span>备注</span><textarea v-model="form.note" rows="3" required @input="markDirty" /></label>
      <section class="card">
        <h2>分账规则</h2>
        <p v-if="!ruleMode" class="hint">这笔历史账单只有最终承担金额，无法还原原规则。选择“改用规则分账”后才会覆盖它。</p>
        <button v-if="!ruleMode" type="button" class="secondary" @click="ruleMode = true; markDirty()">改用规则分账</button>
        <template v-else>
          <h3>单独承担项</h3>
          <div v-for="(charge, index) in form.individual_charges" :key="index" class="charge-row">
            <select v-model="charge.member_id" @change="markDirty"><option v-for="member in members" :key="member.member_id" :value="member.member_id">{{ member.display_name }}</option></select>
            <input :value="(charge.amount_cents / 100).toFixed(2)" type="number" min="0.01" step="0.01" aria-label="单独承担金额" @input="setChargeEvent(index, $event)" />
            <button type="button" class="text-link" @click="removeCharge(index)">移除</button>
          </div>
          <button type="button" class="secondary" @click="addCharge">添加单独承担项</button>
          <h3>余款平摊成员</h3>
          <label v-for="member in members" :key="member.member_id" class="check-row"><input type="checkbox" :checked="form.remainder_member_ids.includes(member.member_id)" @change="toggleRemainder(member.member_id)" />{{ member.display_name }}</label>
        </template>
      </section>
      <p v-if="error" class="alert">{{ error }}</p>
      <section class="card"><h2>每人承担金额</h2><dl class="amount-list"><template v-for="(amount, member) in preview" :key="member"><dt>{{ member }}</dt><dd>{{ money(amount) }}</dd></template></dl></section>
      <button type="button" class="secondary" @click="refreshPreview">预览计算结果</button>
      <button class="primary" :disabled="saving">{{ saving ? '正在保存…' : '保存修改' }}</button>
      <button type="button" class="text-link" @click="cancel">取消</button>
    </form>
  </section>
</template>
