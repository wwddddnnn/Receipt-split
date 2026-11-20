<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getMe, getMembers, postReceipt, type ApiError, type Member } from '@/api'

type Photo = { file: File; url: string }

const router = useRouter()
const members = ref<Member[]>([])
const payerId = ref('')
const note = ref('')
const photos = ref<Photo[]>([])
const loadingMembers = ref(true)
const submitting = ref(false)
const error = ref('')
const clarifications = ref<{ field: string; reason: string }[]>([])
const canSubmit = computed(() => !!payerId.value && !!note.value.trim() && photos.value.length > 0 && !submitting.value)

onMounted(async () => {
  try {
    const [identity, available] = await Promise.all([getMe(), getMembers()])
    members.value = available.members
    payerId.value = identity.member.member_id
  } catch (caught) {
    if ((caught as ApiError).code === 'authentication_required') {
      await router.replace('/login')
      return
    }
    error.value = '无法取得成员名单。请检查服务是否正在运行。'
  } finally {
    loadingMembers.value = false
  }
})

function addPhotos(event: Event) {
  const files = Array.from((event.target as HTMLInputElement).files ?? [])
  const rejected = files.find((file) => !['image/jpeg', 'image/png'].includes(file.type))
  if (rejected) {
    error.value = '当前服务只接受 JPEG 或 PNG 图片。请在 iPhone 相机格式中选择“兼容性最佳”，或先转换照片。'
    ;(event.target as HTMLInputElement).value = ''
    return
  }
  const available = 10 - photos.value.length
  if (files.length > available) {
    error.value = '一次最多上传 10 张图片。'
  }
  files.slice(0, available).forEach((file) => photos.value.push({ file, url: URL.createObjectURL(file) }))
  ;(event.target as HTMLInputElement).value = ''
}

function removePhoto(index: number) {
  URL.revokeObjectURL(photos.value[index].url)
  photos.value.splice(index, 1)
}

function newIdempotencyKey() {
  if (crypto.randomUUID) return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const random = Math.floor(Math.random() * 16)
    return (char === 'x' ? random : (random & 0x3) | 0x8).toString(16)
  })
}

async function submit() {
  if (!canSubmit.value) return
  error.value = ''
  clarifications.value = []
  submitting.value = true
  try {
    const receipt = await postReceipt({
      payerId: payerId.value,
      note: note.value.trim(),
      photos: photos.value.map((photo) => photo.file),
      key: newIdempotencyKey(),
    })
    photos.value.forEach((photo) => URL.revokeObjectURL(photo.url))
    photos.value = []
    await router.push(`/receipt/${receipt.receipt_id}`)
  } catch (caught) {
    const apiError = caught as ApiError
    if (apiError.code === 'receipt_needs_clarification') {
      error.value = '这张收据无法自动入账。当前版本暂不支持编辑草稿，请核对照片和备注后重新提交。'
      const draft = (apiError.details as { draft?: { needs_clarification?: unknown } } | undefined)?.draft
      if (Array.isArray(draft?.needs_clarification)) {
        clarifications.value = draft.needs_clarification.filter(
          (item): item is { field: string; reason: string } =>
            typeof item === 'object' && item !== null &&
            typeof (item as { field?: unknown }).field === 'string' &&
            typeof (item as { reason?: unknown }).reason === 'string',
        )
      }
    } else {
      error.value = apiError.message
    }
  } finally {
    submitting.value = false
  }
}

onBeforeUnmount(() => photos.value.forEach((photo) => URL.revokeObjectURL(photo.url)))
</script>

<template>
  <section class="page">
    <div class="eyebrow">快速记账</div>
    <h1>拍下收据，写一句怎么分。</h1>
    <p class="intro">识别成功后会直接入账。请先确认照片和备注无误。</p>

    <div v-if="error" class="alert" role="alert">
      <p>{{ error }}</p>
      <ul v-if="clarifications.length" class="clarifications">
        <li v-for="item in clarifications" :key="`${item.field}-${item.reason}`">
          <b>{{ item.field }}：</b>{{ item.reason }}
        </li>
      </ul>
    </div>
    <div v-if="loadingMembers" class="loading">正在载入成员名单…</div>

    <form v-else class="form" @submit.prevent="submit">
      <label>
        <span>垫付人</span>
        <select v-model="payerId" :disabled="submitting">
          <option v-for="member in members" :key="member.member_id" :value="member.member_id">{{ member.display_name }}</option>
        </select>
      </label>

      <div>
        <span class="label">收据照片</span>
        <div class="upload-options">
          <label class="upload-area">
            <input type="file" accept="image/jpeg,image/png" capture="environment" :disabled="submitting" @change="addPhotos" />
            <strong>拍照</strong>
            <small>使用后置相机拍摄收据</small>
          </label>
          <label class="upload-area">
            <input type="file" accept="image/jpeg,image/png" multiple :disabled="submitting" @change="addPhotos" />
            <strong>从相册选择</strong>
            <small>可一次选择多张收据</small>
          </label>
        </div>
        <small>最多 10 张；JPEG/PNG，每张不超过 10 MB</small>
        <div v-if="photos.length" class="previews" aria-label="已选择的收据照片">
          <figure v-for="(photo, index) in photos" :key="photo.url">
            <img :src="photo.url" :alt="`收据照片 ${index + 1}`" />
            <button type="button" class="remove" :disabled="submitting" :aria-label="`移除照片 ${index + 1}`" @click="removePhoto(index)">×</button>
          </figure>
        </div>
      </div>

      <label>
        <span>备注</span>
        <textarea v-model="note" rows="4" maxlength="500" :disabled="submitting" placeholder="例如：我垫付，我、BenTest 和 CindyTest 平摊。"></textarea>
        <small>“我”始终指当前登录成员；垫付人可单独选择。</small>
      </label>

      <button class="primary" type="submit" :disabled="!canSubmit">
        {{ submitting ? '正在识别并入账…' : '提交并自动入账' }}
      </button>
      <p class="form-note">识别不完整时不会写入账本，照片和备注会保留在本页以便重试。</p>
    </form>
  </section>
</template>
