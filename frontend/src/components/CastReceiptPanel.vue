<template>
  <section class="cast-lab" data-testid="cast-receipt-panel">
    <div class="cast-head">
      <div>
        <h3>转型受理回执与在途资格</h3>
        <p class="requirement-text" data-testid="cast-requirement">
          取值转型只接收由此核准的值，并按字段展平路径记录中获准的轮次，将实际命中的原值和来源关联到转型后字段。
          旧作业完成后，新路径的 9 已取得转型受理回执、尚未执行。解析流水线的新版上线和回滚之后都可核对同一转型回执；
          资格已经改变的请求可返回选择路径，原来发出的结果仍有凭据。消费保留路径表达式原文、匹配位置及实际行，
          不同版本不能在同一批混接。转换失败的结论写入隔离台账并指出文档键，失败后可恢复当时输入。
        </p>
      </div>
      <div class="cast-actions">
        <RouterLink class="btn" data-testid="goto-rollback-from-cast"
          to="/nested_flatten/deploy?receipt=CR-NEW-AB-9">
          前往另一页回滚部署
        </RouterLink>
        <button class="btn" data-testid="reload-receipts" type="button" @click="refresh">刷新回执</button>
        <button class="btn ghost" data-testid="failure-drill" type="button" @click="failureDrill">
          演练失败隔离与输入恢复
        </button>
      </div>
    </div>

    <nav class="lab-tabs" data-testid="cast-page-tabs">
      <RouterLink class="tab-link" data-testid="cast-receipts-tab" to="/value_cast/receipts">转型回执页</RouterLink>
      <RouterLink class="tab-link" data-testid="cast-isolation-tab" to="/value_cast/isolation">隔离台账</RouterLink>
      <RouterLink class="tab-link" data-testid="goto-rollback-tab" to="/nested_flatten/deploy?receipt=CR-NEW-AB-9">回滚部署页</RouterLink>
    </nav>

    <p v-if="message" class="lab-message" data-testid="cast-message">{{ message }}</p>
    <p v-if="error" class="error-text" data-testid="cast-error">{{ error }}</p>

    <div class="receipt-grid">
      <article
        v-for="receipt in receipts"
        :key="receipt.receipt_id"
        class="receipt-card"
        :class="{ conflict: receipt.version_conflict, done: receipt.status === '已完成', highlight: highlightId === receipt.receipt_id }"
        :data-testid="`receipt-${receipt.receipt_id}`"
        :data-status="receipt.status"
      >
        <header>
          <strong data-testid="receipt-id">{{ receipt.receipt_id }}</strong>
          <span class="status-pill">{{ receipt.status }}</span>
        </header>
        <dl class="receipt-meta">
          <div><dt>批次</dt><dd>{{ receipt.batch_id }}</dd></div>
          <div><dt>路径版本/轮次</dt><dd>{{ receipt.path_version }} #{{ receipt.approved_round }}</dd></div>
          <div><dt>表达式原文</dt><dd><code>{{ receipt.expression }}</code></dd></div>
          <div><dt>受理原值</dt><dd class="raw-value" data-testid="receipt-raw-value">{{ receipt.raw_value }}</dd></div>
          <div><dt>转型后字段</dt><dd><code>{{ receipt.target_field }}</code> =
            <strong data-testid="receipt-transformed-value">{{ receipt.transformed_value ?? '未执行' }}</strong>
          </dd></div>
          <div><dt>当前规则</dt><dd>{{ receipt.current_rule_version || ruleVersion }}</dd></div>
          <div><dt>凭据</dt><dd class="credential" :title="receipt.credential">{{ receipt.credential }}</dd></div>
        </dl>

        <div v-if="receipt.version_conflict && receipt.selection_required"
             class="conflict-box" data-testid="qualification-conflict">
          <p><strong>版本冲突 / 资格已经改变：</strong>在途回执固定 {{ receipt.path_version }}，当前规则已回滚为 {{ receipt.current_rule_version }}。请求可返回选择路径；请选择：</p>
          <div class="choice-buttons">
            <button class="btn primary" data-testid="adopt-pinned-9" type="button"
              @click="selectPath(receipt.receipt_id, 'pinned')">
              采用原待执行的 9
            </button>
            <button class="btn" data-testid="adopt-legacy-7" type="button"
              @click="selectPath(receipt.receipt_id, 'legacy')">
              回到已核准旧路径 7
            </button>
          </div>
        </div>

        <div class="receipt-buttons">
          <button class="btn" :data-testid="`claim-${receipt.receipt_id}`" type="button"
            @click="claim(receipt.receipt_id)">
            领取回执
          </button>
          <button class="btn primary" :data-testid="`continue-${receipt.receipt_id}`" type="button"
            @click="continueReceipt(receipt.receipt_id)">
            从转型回执继续处理
          </button>
          <button class="btn ghost" :data-testid="`open-location-${receipt.receipt_id}`" type="button"
            @click="openLocation(receipt)">
            打开实际命中的位置
          </button>
        </div>

        <div v-if="receipt.field_sources && Object.keys(receipt.field_sources).length"
             class="source-proof" data-testid="source-proof">
          <h5>转型字段来源关联</h5>
          <pre>{{ JSON.stringify(receipt.field_sources, null, 2) }}</pre>
        </div>
      </article>
    </div>

    <section class="ledger" data-testid="isolation-ledger">
      <h4>隔离台账 / 失败输入恢复</h4>
      <table class="mini-table">
        <thead><tr><th>隔离编号</th><th>文档键</th><th>字段</th><th>失败原因</th><th>恢复</th></tr></thead>
        <tbody>
          <tr v-for="item in isolationRows" :key="item.隔离编号" data-testid="isolation-row">
            <td>{{ item.隔离编号 }}</td>
            <td data-testid="isolation-doc-key">{{ item.文档键 }}</td>
            <td>{{ item.失败字段 }}</td>
            <td>{{ item.失败原因 }}</td>
            <td><button class="link" :data-testid="`recover-${item.隔离编号}`" type="button"
              @click="recover(item.隔离编号)">恢复当时输入</button></td>
          </tr>
          <tr v-if="!isolationRows.length"><td colspan="5" class="muted">暂无转型隔离记录</td></tr>
        </tbody>
      </table>
      <pre v-if="recoveredInput" class="recovered" data-testid="recovered-input">{{ recoveredInput }}</pre>
    </section>

    <div v-if="location" class="modal-backdrop" data-testid="source-location-dialog" @click.self="location = null">
      <div class="modal location-modal">
        <h3>实际命中的原文档位置</h3>
        <dl>
          <div><dt>文档键</dt><dd data-testid="location-doc-key">{{ location.doc_key }}</dd></div>
          <div><dt>路径原文</dt><dd><code>{{ location.expression }}</code></dd></div>
          <div><dt>匹配位置</dt><dd data-testid="location-position"><code>{{ location.position || location.canonical_position }}</code></dd></div>
          <div><dt>JSON Pointer</dt><dd><code>{{ location.json_pointer }}</code></dd></div>
          <div><dt>实际行号</dt><dd data-testid="location-line">{{ location.line_number || '未知' }}</dd></div>
          <div><dt>原值</dt><dd data-testid="location-value">{{ location.value ?? location.raw_value }}</dd></div>
        </dl>
        <pre class="actual-line" data-testid="location-actual-line">{{ location.actual_line || '未保存源文本行' }}</pre>
        <div class="page-actions">
          <RouterLink class="btn" :to="`/nested_flatten/confirm?reference=${location.confirmation_id || ''}`">
            到路径确认页定位
          </RouterLink>
          <button class="btn primary" type="button" @click="location = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { pathWorkbenchApi, type JsonRecord } from '@/api/path-workbench'

const route = useRoute()
const receipts = ref<JsonRecord[]>([])
const isolationRows = ref<JsonRecord[]>([])
const ruleVersion = ref('')
const highlightId = ref(String(route.query.receipt || 'CR-NEW-AB-9'))
const message = ref('')
const error = ref('')
const location = ref<JsonRecord | null>(null)
const recoveredInput = ref('')

async function refresh() {
  error.value = ''
  try {
    const scenario = await pathWorkbenchApi.getScenario()
    receipts.value = scenario.receipts?.length
      ? scenario.receipts
      : (await pathWorkbenchApi.receipts()).items
    ruleVersion.value = scenario.rule_state?.active_version ?? ''
    isolationRows.value = (await pathWorkbenchApi.isolation()).items
  } catch (e) {
    error.value = e instanceof Error ? e.message : '回执读取失败'
  }
}

function showResult(result: JsonRecord, fallback: string) {
  message.value = result.message || fallback
  if (result.receipt?.receipt_id) {
    const index = receipts.value.findIndex((r) => r.receipt_id === result.receipt.receipt_id)
    if (index >= 0) receipts.value[index] = result.receipt
  }
}

async function claim(id: string) {
  error.value = ''
  message.value = ''
  try {
    const result = await pathWorkbenchApi.claim(id, { claim_time: new Date().toISOString() })
    showResult(result, '已领取')
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '领取失败'
  }
}

async function continueReceipt(id: string) {
  error.value = ''
  message.value = ''
  try {
    const result = await pathWorkbenchApi.execute(id, {
      selection: 'pinned',
      executor: 'next-shift-ui',
      execute_time: new Date().toISOString(),
    })
    showResult(result, '已续做')
    if (!result.ok) error.value = result.message
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '续做失败'
    await refresh()
  }
}

async function selectPath(id: string, selection: 'pinned' | 'legacy') {
  error.value = ''
  message.value = ''
  try {
    const result = await pathWorkbenchApi.select(id, {
      selection,
      claimant: 'next-shift-ui',
      select_time: new Date().toISOString(),
    })
    showResult(result, '已选择并执行')
    if (!result.ok) error.value = result.message
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '路径选择失败'
    await refresh()
  }
}

async function openLocation(receipt: JsonRecord) {
  error.value = ''
  try {
    const reference = receipt.confirmation_id || receipt.target_row_id
    const result = await pathWorkbenchApi.sourceLocation(reference)
    location.value = { ...result.location, confirmation_id: receipt.confirmation_id }
  } catch (e) {
    // 兜底：回执自身已经保存了位置和实际行。
    location.value = receipt.source || receipt
    error.value = e instanceof Error ? `位置接口不可用，已展示回执快照：${e.message}` : ''
  }
}

async function recover(id: string) {
  error.value = ''
  recoveredInput.value = ''
  try {
    const result = await pathWorkbenchApi.recover(id)
    recoveredInput.value = JSON.stringify(result.input_snapshot, null, 2)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '恢复失败'
  }
}

async function failureDrill() {
  error.value = ''
  message.value = ''
  try {
    const doc = { broken: { value: 'not-an-int' } }
    await pathWorkbenchApi.confirm({
      confirmation_id: 'PC-FAIL-DRILL',
      flatten_no: 'NF-FAIL-DRILL',
      expression: '$.broken.value',
      path_version: 'v1',
      document: doc,
      batch_id: 'BATCH-FAIL-DRILL',
      target_field: 'value',
      approved_round: 1,
      confirm_time: new Date().toISOString(),
    })
    const confirmations = await pathWorkbenchApi.confirmations('')
    const confirmation = confirmations.items.find((c: JsonRecord) => c.confirmation_id === 'PC-FAIL-DRILL')
    if (!confirmation) throw new Error('失败演练路径确认未返回')
    const accepted = await pathWorkbenchApi.createReceipt({
      batch_id: 'BATCH-FAIL-DRILL',
      confirmation_id: confirmation.confirmation_id,
      receipt_id: 'CR-FAIL-DRILL',
      cast_no: 'VC-003',
      target_field: 'value',
      approved_round: 1,
      target_type: 'int64',
    })
    const executed = await pathWorkbenchApi.execute(accepted.receipt.receipt_id, {
      executor: 'drill',
      execute_time: new Date().toISOString(),
    })
    message.value = executed.message
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '演练失败'
    await refresh()
  }
}

onMounted(refresh)
</script>

<style scoped>
.cast-lab { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin: 12px 0 16px; }
.cast-head { display: flex; justify-content: space-between; gap: 14px; align-items: flex-start; }
.cast-head p { color: var(--muted); font-size: 13px; max-width: 780px; }
.cast-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.lab-message { background: #ecfdf3; border: 1px solid #abefc6; color: #067647; padding: 8px 10px; border-radius: 6px; font-size: 13px; }
.lab-tabs { display: flex; gap: 8px; margin: 10px 0; flex-wrap: wrap; }
.tab-link { border: 1px solid var(--border); border-radius: 999px; padding: 5px 12px; font-size: 13px; text-decoration: none; color: var(--brand); background: #f8fafc; }
.receipt-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 12px; margin: 12px 0; }
.receipt-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: #fcfcfd; }
.receipt-card.conflict { border-color: #f79009; box-shadow: 0 0 0 2px rgb(247 144 9 / 12%); }
.receipt-card.done { border-color: #12b76a; }
.receipt-card.highlight { outline: 3px solid rgb(31 111 235 / 22%); }
.receipt-card header { display: flex; justify-content: space-between; align-items: center; }
.status-pill { border-radius: 999px; background: #eef2f6; padding: 2px 9px; font-size: 12px; }
.conflict .status-pill { background: #fffaeb; color: #b54708; }
.done .status-pill { background: #ecfdf3; color: #067647; }
.receipt-meta { display: grid; gap: 6px; margin: 10px 0; font-size: 12px; }
.receipt-meta > div { display: grid; grid-template-columns: 92px 1fr; gap: 8px; }
.receipt-meta dt { color: var(--muted); }
.receipt-meta dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.raw-value { font-size: 16px; font-weight: 700; color: #175cd3; }
.credential { font-family: monospace; color: var(--muted); }
.conflict-box { background: #fffaeb; border: 1px solid #fedf89; border-radius: 6px; padding: 8px; font-size: 12px; }
.choice-buttons, .receipt-buttons { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.source-proof { margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 8px; }
.source-proof pre, .recovered { max-height: 180px; overflow: auto; background: #101828; color: #d0d5dd; padding: 8px; border-radius: 6px; font-size: 11px; white-space: pre-wrap; }
.ledger { margin-top: 16px; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mini-table th, .mini-table td { border: 1px solid var(--border); padding: 6px; text-align: left; vertical-align: top; }
.muted { color: var(--muted); }
.modal-backdrop { position: fixed; inset: 0; background: rgb(15 23 42 / 35%); display: grid; place-items: center; z-index: 30; padding: 20px; }
.location-modal { width: min(680px, 100%); background: #fff; border-radius: 12px; padding: 18px; max-height: 90vh; overflow: auto; }
.location-modal dl > div { display: grid; grid-template-columns: 110px 1fr; gap: 10px; margin: 8px 0; font-size: 13px; }
.location-modal dt { color: var(--muted); }
.actual-line { background: #f2f4f7; padding: 10px; border-radius: 6px; }
</style>
