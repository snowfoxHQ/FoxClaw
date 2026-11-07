<script setup>
import { computed } from 'vue'
import DocViewer from './DocViewer.vue'
import MessageStream from './MessageStream.vue'
import { chat } from '../stores/chat.js'
import { state, MODULES, moduleLabel, setModule, closeDoc, openDoc } from '../stores/app.js'

const meta = computed(() => MODULES.find((m) => m.id === state.module) || MODULES[0])

/* P0 演示样本:让「文字版 / CLI 版」两套皮肤可直接验收。
   P2 换成真实打开(知识条目正文 / 技能脚本 / 摄入的原始文件)。 */
const SAMPLE = {
  text: {
    id: 'demo-text',
    kind: 'text',
    title: '间隔重复的排程边界',
    path: 'knowledge/学习法/间隔重复.md',
    meta: '知识条目 · 2026-11-06',
    content: `间隔重复的核心不是「复习得越多越好」,而是把每一次复习压在遗忘曲线的临界点上。

临界点偏早,复习成本低但对记忆巩固的贡献小;偏晚则已经遗忘,需要重新学习。所以排程算法真正的目标是:估计当前的遗忘概率,把它拉回一个固定区间。

实践上受两个边界约束。

一是时间粒度。日级排程无法利用同一天内的遗忘差异,因此短周期复习(1 天、3 天)必然粗糙,收益主要来自中长周期。

二是材料难度。同一张卡片在不同阶段的难度不同,固定间隔会同时对简单卡过度复习、对难卡复习不足。

合理做法是:用固定间隔做冷启动,再用每次复习的反馈(用时、正确率、自信度)在线修正每张卡的半衰期。冷启动保证可用,在线修正保证收敛。`,
  },
  code: {
    id: 'demo-code',
    kind: 'code',
    lang: 'python',
    title: 'scheduler.py',
    path: 'snowfoxclaw/learning/scheduler.py',
    meta: 'Python · 上次修改 2 天前',
    content: `from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Card:
    id: str
    half_life_hours: float = 24.0
    last_review: datetime | None = None


def forgetting_probability(card: Card, now: datetime) -> float:
    """Ebbinghaus 近似:当前遗忘概率,0 = 刚复习完,1 = 彻底忘了。"""
    if card.last_review is None:
        return 1.0
    elapsed = (now - card.last_review).total_seconds() / 3600.0
    return 1.0 - 2 ** (-elapsed / max(card.half_life_hours, 0.5))


def next_due(card: Card, now: datetime, target: float = 0.3) -> datetime:
    """求把遗忘概率拉回 target 的那一刻 —— 直接解上面的反函数。"""
    if card.last_review is None:
        return now
    hl = max(card.half_life_hours, 0.5)
    hours = -hl * math.log2(1.0 - target)
    return card.last_review + timedelta(hours=hours)


def rescale(card: Card, ratio: float) -> Card:
    """复习反馈 → 半衰期在线修正。ratio > 1 代表比预期轻松,半衰期拉长。"""
    return Card(
        id=card.id,
        half_life_hours=min(max(card.half_life_hours * ratio, 0.5), 24 * 365),
        last_review=card.last_review,
    )
`,
  },
}
</script>

<template>
  <section class="stage">
    <div class="tabs">
      <button
        class="tab"
        :class="{ on: !state.doc && state.module === 'overview' }"
        @click="setModule('overview')"
      >
        核心
      </button>
      <button
        v-if="state.module !== 'overview'"
        class="tab"
        :class="{ on: !state.doc }"
        @click="closeDoc()"
      >
        {{ moduleLabel(state.module) }}
      </button>
      <button v-if="state.doc" class="tab on doc" @click="closeDoc()">
        <span class="t">{{ state.doc.title }}</span>
        <span class="x" title="关闭">✕</span>
      </button>
    </div>

    <div class="stage-body">
      <DocViewer v-if="state.doc" :doc="state.doc" />

      <div v-else-if="state.module !== 'overview'" class="mod-ph">
        <div class="ph-head">
          <h2>{{ meta.label }}</h2>
          <span class="tag">{{ meta.tag }}</span>
        </div>
        <p class="note">{{ meta.label }} 面板在 P2 从 deck/main.js 迁入,接口契约不变。</p>
        <div class="sk">
          <i v-for="n in 4" :key="n" :style="{ width: 92 - n * 12 + '%' }" />
        </div>
      </div>

      <div v-else class="core">
        <MessageStream v-if="chat.messages.length" />
        <div v-else class="core-empty">
          <div class="orb" />
          <div class="core-cap">问我任何事 · 主管 Agent 会按需调度</div>
          <div class="demo">
            <button @click="openDoc(SAMPLE.text)">看文字版</button>
            <button class="dark" @click="openDoc(SAMPLE.code)">看 CLI 代码版</button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stage { display: flex; flex-direction: column; min-height: 0; }

.tabs {
  display: flex; align-items: center; gap: 4px;
  padding: 8px 10px 0; flex-shrink: 0;
  overflow-x: auto;
}
.tab {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 11px; border-radius: var(--r-sm) var(--r-sm) 0 0;
  font-size: 12px; color: var(--dim);
  border: 1px solid transparent; border-bottom: 0;
  white-space: nowrap;
}
.tab:hover { color: var(--ink); background: rgba(255, 255, 255, 0.5); }
.tab.on {
  color: var(--ink); font-weight: 600;
  background: rgba(255, 255, 255, 0.86);
  border-color: var(--line-soft);
}
.tab.doc { max-width: 260px; }
.tab.doc .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tab.doc .x { font-size: 10px; color: var(--faint); }
.tab.doc .x:hover { color: var(--red); }

.stage-body {
  flex: 1; min-height: 0;
  margin: 0 8px 8px;
  border-radius: 0 var(--r-mid) var(--r-mid) var(--r-mid);
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid var(--line-soft);
  overflow: hidden;
  display: flex;
}

/* ── 核心:空态居中,有消息时整块交给消息流 ── */
.core { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.core-empty {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 16px;
  position: relative;
}
.orb {
  width: 190px; height: 190px; border-radius: 50%;
  background: radial-gradient(circle at 34% 30%, #ffffff, #cfe8c8 42%, #9ecd9f 74%, #6fae86 100%);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.8), 0 22px 60px rgba(26, 157, 95, 0.24),
    inset 0 -18px 40px rgba(20, 90, 60, 0.18);
  animation: breathe 6s ease-in-out infinite;
}
@keyframes breathe {
  50% { transform: scale(1.03); }
}
.core-cap { font-size: 11.5px; color: var(--faint); }
.demo { display: flex; gap: 8px; }
.demo button {
  font-size: 12px; color: var(--dim);
  padding: 7px 13px; border-radius: var(--r-pill);
  background: #fff; border: 1px solid var(--line);
  transition: color 0.15s, transform 0.1s;
}
.demo button:hover { color: var(--ink); }
.demo button:active { transform: scale(0.98); }
.demo button.dark {
  background: var(--cli-bg); color: var(--cli-ink); border-color: var(--cli-bg);
  font-family: var(--mono);
}

/* ── 模块占位 ── */
.mod-ph { flex: 1; padding: 20px 22px; overflow-y: auto; }
.ph-head { display: flex; align-items: baseline; gap: 9px; }
.ph-head h2 { font-size: 17px; font-weight: 700; letter-spacing: -0.01em; }
.ph-head .tag { font-size: 10.5px; color: var(--faint); font-family: var(--mono); }
.note { margin-top: 8px; font-size: 12px; color: var(--dim); }
.sk { margin-top: 20px; display: flex; flex-direction: column; gap: 9px; }
.sk i { display: block; height: 34px; border-radius: var(--r-sm); background: var(--surface-2); }
.sk i:nth-child(2n) { opacity: 0.6; }
</style>
