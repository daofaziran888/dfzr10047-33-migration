<template>
  <section class="path-lab" data-testid="path-migration-panel">
    <div class="path-lab-head">
      <div>
        <h3>点路径迁移与路径确认</h3>
        <p class="requirement-text" data-testid="migration-requirement">
          把嵌套展平页的旧式点路径迁移到支持字面点号的可转义版本。服务端按版本校验并保存字段展平路径记录；
          历史旧路径仍解析为原来的嵌套值，不能受到新增转义语法影响。当前对照文档同时录入嵌套
          <code>a</code> 下的 <code>b</code> 为 <strong>7</strong>、字面键
          <code>a.b</code> 为 <strong>9</strong>。
        </p>
      </div>
      <div class="path-lab-actions">
        <button class="btn primary" data-testid="seed-literal-dot-case" type="button" @click="resetScenario">
          录入 7 / 9 对照场景
        </button>
        <button class="btn" data-testid="reload-path-scenario" type="button" @click="loadScenario">
          重新核对场景
        </button>
        <button class="btn" data-testid="remigrate-path" type="button" @click="remigrate">
          重新发起路径迁移
        </button>
        <RouterLink class="btn primary" data-testid="goto-cast-receipts" to="/value_cast?receipt=CR-NEW-AB-9">
          前往转型回执处理 9
        </RouterLink>
      </div>
    </div>

    <nav class="lab-tabs" data-testid="path-page-tabs">
      <RouterLink class="tab-link" data-testid="path-workbench-tab" to="/nested_flatten">迁移工作台</RouterLink>
      <RouterLink class="tab-link" data-testid="path-confirm-tab" to="/nested_flatten/confirm">路径确认页</RouterLink>
      <RouterLink class="tab-link" data-testid="rollback-deploy-tab" to="/nested_flatten/deploy">回滚部署页</RouterLink>
    </nav>

    <p v-if="message" class="lab-message" data-testid="path-message">{{ message }}</p>
    <p v-if="error" class="error-text" data-testid="path-error">{{ error }}</p>

    <div class="rule-bar" data-testid="path-rule-bar" :data-panel="panelName">
      <h4 v-if="panelName === 'deploy'" data-testid="rollback-deploy-page">回滚部署页</h4>
      <span>当前编辑器/解析规则版本：</span>
      <strong data-testid="active-rule-version">{{ ruleVersion }}</strong>
      <span class="rule-status">{{ scenario?.rule_state?.status ?? '' }}</span>
      <button
        class="btn ghost"
        data-testid="deploy-escape-rule"
        type="button"
        @click="deployV2"
      >
        重新上线新版解析规则
      </button>
      <button
        class="btn danger"
        data-testid="rollback-editor-rule"
        type="button"
        @click="rollback"
      >
        回滚编辑器规则到旧式点路径
      </button>
      <RouterLink class="btn" data-testid="goto-rollback-page" to="/nested_flatten/deploy">
        在部署页查看回滚
      </RouterLink>
    </div>

    <form class="custom-preview" data-testid="custom-path-form" @submit.prevent="runCustomPreview">
      <label>
        <span>路径表达式原文</span>
        <input v-model="customExpression" data-testid="custom-path-expression" />
      </label>
      <label class="version-field">
        <span>按版本校验</span>
        <select v-model="customVersion" data-testid="custom-path-version">
          <option value="v1">dot-v1 旧式点路径</option>
          <option value="v2">escape-v2 可转义路径</option>
        </select>
      </label>
      <label class="doc-field">
        <span>原文档 JSON</span>
        <textarea v-model="customDocumentText" rows="4" data-testid="custom-document-json" />
      </label>
      <div class="custom-actions">
        <button class="btn" type="submit" data-testid="run-path-preview">预览匹配位置</button>
        <button class="btn primary" type="button" data-testid="confirm-custom-path" @click="confirmCustom">
          确认并保存此路径
        </button>
      </div>
    </form>
    <p v-if="customError" class="error-text" data-testid="custom-path-error">{{ customError }}</p>
    <div v-if="customPreview" class="custom-result" data-testid="custom-preview-result">
      命中 {{ customPreview.match_count }} 处；首个值：
      <strong>{{ customPreview.matches?.[0]?.value }}</strong>
      <code>{{ customPreview.matches?.[0]?.position }}</code>
      <span>第 {{ customPreview.matches?.[0]?.line_number || '?' }} 行</span>
    </div>

    <h4 v-if="panelName === 'confirm'" class="confirm-page-title" data-testid="path-confirm-page">
      路径确认页：展示匹配到的原文档位置；旧路径和新路径的预览结果能够并列选择
    </h4>

    <div v-if="preview" class="preview-grid" data-testid="path-preview-grid">
      <article
        class="preview-card old"
        :class="{ selected: selectedSide === 'old' }"
        data-testid="preview-old-path"
      >
        <header>
          <label>
            <input v-model="selectedSide" type="radio" value="old" data-testid="select-old-path" />
            旧路径 / dot-v1
          </label>
          <span class="value-badge" data-testid="old-path-value">{{ preview.old?.value }}</span>
        </header>
        <code class="expr" data-testid="old-path-expression">{{ preview.old?.expression }}</code>
        <p class="hint">历史表达式不识别转义语法，仍进入嵌套 a → b。</p>
        <ul>
          <li v-for="(match, index) in preview.old?.matches ?? []" :key="`old-${index}`"
              :data-testid="`old-match-${index}`">
            <span>位置：<code>{{ match.position }}</code></span>
            <span>文档键：<code>{{ match.doc_key }}</code></span>
            <span>实际行：第 {{ match.line_number || '?' }} 行</span>
            <pre data-testid="old-actual-line">{{ match.actual_line || '（单行输入，未提供源文本）' }}</pre>
          </li>
        </ul>
      </article>

      <article
        class="preview-card new"
        :class="{ selected: selectedSide === 'new' }"
        data-testid="preview-new-path"
      >
        <header>
          <label>
            <input v-model="selectedSide" type="radio" value="new" data-testid="select-new-path" />
            新路径 / escape-v2
          </label>
          <span class="value-badge" data-testid="new-path-value">{{ preview.new?.value }}</span>
        </header>
        <code class="expr" data-testid="new-path-expression">{{ preview.new?.expression }}</code>
        <p class="hint"><code>\.</code> 不切层级，因此命中字面键 “a.b”。</p>
        <ul>
          <li v-for="(match, index) in preview.new?.matches ?? []" :key="`new-${index}`"
              :data-testid="`new-match-${index}`">
            <span>位置：<code>{{ match.position }}</code></span>
            <span>文档键：<code>{{ match.doc_key }}</code></span>
            <span>实际行：第 {{ match.line_number || '?' }} 行</span>
            <pre data-testid="new-actual-line">{{ match.actual_line || '（单行输入，未提供源文本）' }}</pre>
          </li>
        </ul>
      </article>
    </div>

    <div class="confirm-actions">
      <button class="btn primary" data-testid="confirm-selected-path" type="button" @click="confirmSelected">
        确认并用服务端结果保存所选路径
      </button>
      <span>当前并列选择：<strong data-testid="selected-path-side">{{ selectedSide }}</strong></span>
    </div>

    <div class="two-col">
      <section data-testid="confirmation-list">
        <h4>已采用的字段展平路径确认</h4>
        <table class="mini-table">
          <thead>
            <tr><th>编号</th><th>版本/轮次</th><th>表达式原文</th><th>值</th><th>位置/实际行</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in confirmations" :key="item.confirmation_id"
                :data-reference="item.confirmation_id"
                :class="{ highlight: reference === item.confirmation_id }"
                data-testid="confirmation-row">
              <td>{{ item.confirmation_id }}</td>
              <td>{{ item.path_version }} #{{ item.approved_round }}</td>
              <td><code>{{ item.expression }}</code></td>
              <td data-testid="confirmation-value">{{ item.matched_value }}</td>
              <td>
                <div>{{ item.position }}</div>
                <div class="muted">{{ item.doc_key }} · 第 {{ item.line_number || '?' }} 行</div>
                <pre class="line-pre">{{ item.actual_line }}</pre>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section data-testid="target-row-list">
        <h4>幂等目标行</h4>
        <table class="mini-table">
          <thead><tr><th>目标行</th><th>值</th><th>消费状态</th></tr></thead>
          <tbody>
            <tr v-for="row in targetRows" :key="row.target_row_id" data-testid="target-row">
              <td>{{ row.target_row_id }}</td>
              <td>{{ row.raw_value }}</td>
              <td>{{ row.consumed ? `已由 ${row.consumed_by_receipt} 消费` : '待消费' }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { pathWorkbenchApi, type JsonRecord } from '@/api/path-workbench'

const props = defineProps<{ reference?: string }>()
const route = useRoute()
const scenario = ref<JsonRecord | null>(null)
const confirmations = ref<JsonRecord[]>([])
const targetRows = ref<JsonRecord[]>([])
const selectedSide = ref<'old' | 'new'>('new')
const message = ref('')
const error = ref('')
const reference = ref(props.reference || String(route.query.reference || ''))
const customExpression = ref(String.raw`$.a\.b`)
const customVersion = ref<'v1' | 'v2'>('v2')
const customDocumentText = ref(JSON.stringify({ a: { b: 7 }, 'a.b': 9 }, null, 2))
const customPreview = ref<JsonRecord | null>(null)
const customError = ref('')

const preview = computed(() => scenario.value?.preview ?? null)
const ruleVersion = computed(() => scenario.value?.rule_state?.active_version ?? '加载中')
const panelName = computed(() => String(route.params.panel || route.query.view || 'workbench'))

watch(() => route.query.reference, (value) => {
  reference.value = String(value || '')
})

async function loadScenario() {
  error.value = ''
  try {
    scenario.value = await pathWorkbenchApi.getScenario()
    const [conf, targets] = await Promise.all([
      pathWorkbenchApi.confirmations(),
      pathWorkbenchApi.targetRows(),
    ])
    confirmations.value = conf.items
    targetRows.value = targets.items
  } catch (e) {
    error.value = e instanceof Error ? e.message : '场景加载失败'
  }
}

async function resetScenario() {
  error.value = ''
  message.value = ''
  try {
    scenario.value = await pathWorkbenchApi.scenario()
    message.value = '已录入嵌套 a.b=7 与字面键 a.b=9，并将规则恢复为新版'
    await loadScenario()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '场景录入失败'
  }
}

async function remigrate() {
  message.value = ''
  error.value = ''
  try {
    const result = await pathWorkbenchApi.migrate({})
    message.value = result.message
    await loadScenario()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '迁移核对失败'
  }
}

async function rollback() {
  message.value = ''
  error.value = ''
  try {
    const result = await pathWorkbenchApi.rollback({
      rollback_time: new Date().toISOString(),
      operator: 'maintainer-ui',
    })
    message.value = result.message
    await loadScenario()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '回滚失败'
  }
}

async function deployV2() {
  message.value = ''
  error.value = ''
  try {
    const result = await pathWorkbenchApi.deploy({
      version: 'v2',
      deploy_time: new Date().toISOString(),
    })
    message.value = result.message
    await loadScenario()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '上线失败'
  }
}

async function confirmSelected() {
  message.value = ''
  error.value = ''
  const side = selectedSide.value
  const candidate = preview.value?.[side]
  if (!candidate?.matches?.[0]) {
    error.value = '当前路径没有匹配位置'
    return
  }
  try {
    const result = await pathWorkbenchApi.confirm({
      expression: candidate.expression,
      path_version: candidate.path_version,
      document: scenario.value?.document ?? { a: { b: 7 }, 'a.b': 9 },
      source_text: scenario.value?.source_text,
      doc_key: 'DOC-A-DOT-B',
      batch_id: side === 'old' ? 'BATCH-OLD-AB-7' : 'BATCH-NEW-AB-9',
      target_field: 'b',
      approved_round: side === 'old' ? 1 : 2,
      choice_group: 'CG-AB-LITERAL',
      confirmation_id: side === 'old' ? 'PC-OLD-AB-7' : 'PC-NEW-AB-9',
      flatten_no: side === 'old' ? 'NF-OLD-AB' : 'NF-NEW-AB-ESCAPED',
      confirm_time: new Date().toISOString(),
    })
    message.value = result.message
    await loadScenario()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '确认失败'
  }
}

async function runCustomPreview() {
  customError.value = ''
  try {
    const document = JSON.parse(customDocumentText.value) as JsonRecord
    customPreview.value = await pathWorkbenchApi.previewPath({
      expression: customExpression.value,
      path_version: customVersion.value,
      document,
    })
  } catch (e) {
    customPreview.value = null
    customError.value = e instanceof Error ? e.message : 'JSON 或路径预览失败'
  }
}

async function confirmCustom() {
  customError.value = ''
  try {
    const document = JSON.parse(customDocumentText.value) as JsonRecord
    await pathWorkbenchApi.confirm({
      expression: customExpression.value,
      path_version: customVersion.value,
      document,
      batch_id: `BATCH-CUSTOM-${Date.now()}`,
      target_field: 'custom_value',
      approved_round: 1,
      confirm_time: new Date().toISOString(),
    })
    message.value = '自定义路径已经服务端按版本确认并保存'
    await loadScenario()
  } catch (e) {
    customError.value = e instanceof Error ? e.message : '自定义路径确认失败'
  }
}

onMounted(loadScenario)
</script>

<style scoped>
.path-lab { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin: 12px 0 16px; }
.path-lab-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.path-lab-head h3 { margin: 0 0 6px; }
.path-lab-head p { color: var(--muted); font-size: 13px; margin: 0; max-width: 760px; }
.path-lab-actions, .path-lab-actions .btn { white-space: nowrap; }
.path-lab-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.lab-message { background: #ecfdf3; border: 1px solid #abefc6; color: #067647; padding: 8px 10px; border-radius: 6px; font-size: 13px; }
.lab-tabs { display: flex; gap: 8px; margin: 10px 0; flex-wrap: wrap; }
.tab-link { border: 1px solid var(--border); border-radius: 999px; padding: 5px 12px; font-size: 13px; text-decoration: none; color: var(--brand); background: #f8fafc; }
.custom-preview { display: grid; grid-template-columns: 1.4fr .5fr 2fr auto; gap: 10px; align-items: end; background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin: 10px 0; }
.custom-preview label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.custom-preview input, .custom-preview select, .custom-preview textarea { border: 1px solid var(--border); border-radius: 6px; padding: 7px; font: inherit; color: #1f2937; }
.custom-actions { display: flex; flex-direction: column; gap: 8px; }
.custom-result { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; background: #eff8ff; border: 1px solid #b2ddff; border-radius: 6px; padding: 8px 10px; font-size: 13px; margin-bottom: 10px; }
@media (max-width: 1100px) { .custom-preview { grid-template-columns: 1fr; } }
.rule-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin: 12px 0; font-size: 13px; }
.rule-status { color: var(--muted); }
.btn.danger { color: #b42318; border-color: #fda29b; }
.preview-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.preview-card { border: 2px solid var(--border); border-radius: 8px; padding: 12px; background: #fcfcfd; }
.preview-card.selected { border-color: var(--brand); box-shadow: 0 0 0 2px rgb(31 111 235 / 12%); }
.preview-card header { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.expr { display: inline-block; margin: 8px 0; font-size: 15px; background: #f2f4f7; padding: 4px 6px; border-radius: 4px; }
.value-badge { min-width: 34px; text-align: center; border-radius: 999px; padding: 3px 10px; font-weight: 700; background: #eff8ff; color: #175cd3; }
.old .value-badge { background: #fef3f2; color: #b42318; }
.hint { color: var(--muted); font-size: 12px; }
.preview-card ul { padding-left: 0; list-style: none; display: grid; gap: 8px; }
.preview-card li { border-top: 1px dashed var(--border); padding-top: 8px; display: grid; gap: 3px; font-size: 12px; }
pre { white-space: pre-wrap; word-break: break-all; margin: 0; }
.confirm-actions { display: flex; align-items: center; gap: 12px; margin: 12px 0; font-size: 13px; }
.two-col { display: grid; grid-template-columns: 1.3fr .7fr; gap: 12px; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mini-table th, .mini-table td { border: 1px solid var(--border); padding: 6px; vertical-align: top; text-align: left; }
.mini-table tr.highlight { background: #fffaeb; outline: 2px solid #f79009; }
.line-pre { color: var(--muted); max-width: 260px; }
.muted { color: var(--muted); }
@media (max-width: 1100px) { .preview-grid, .two-col { grid-template-columns: 1fr; } }
</style>
