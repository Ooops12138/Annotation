import JXG from 'jsxgraph'
import { parse } from 'mathjs'
import type {
  ComplexPlaneSpec,
  ComponentControlSpec,
  FunctionGraphSpec,
  InteractiveComponentSpec,
  IntervalLineSpec,
} from './document'
import { isInteractiveComponentSpec } from './document'

const allowedFunctions = new Set(['abs', 'cos', 'exp', 'log', 'sin', 'sqrt', 'tan'])
const allowedOperators = new Set(['+', '-', '*', '/', '^'])

type Board = any

export interface InteractiveComponentRenderHandle {
  destroy: () => void
}

function isRestrictedMathNode(node: any): boolean {
  if (!node || typeof node !== 'object') return false
  if (node.type === 'ConstantNode') return typeof node.value === 'number' && Number.isFinite(node.value)
  if (node.type === 'SymbolNode') return node.name === 'x'
  if (node.type === 'ParenthesisNode') return isRestrictedMathNode(node.content)
  if (node.type === 'OperatorNode') {
    return allowedOperators.has(node.op)
      && Array.isArray(node.args)
      && node.args.length >= 1
      && node.args.length <= 2
      && node.args.every(isRestrictedMathNode)
  }
  if (node.type === 'FunctionNode') {
    return node.fn?.type === 'SymbolNode'
      && allowedFunctions.has(node.fn.name)
      && Array.isArray(node.args)
      && node.args.length === 1
      && isRestrictedMathNode(node.args[0])
  }
  return false
}

export function compileRestrictedExpression(formula: string): ((x: number) => number | null) {
  const expression = parse(formula)
  if (!isRestrictedMathNode(expression)) throw new Error('函数表达式不在受限数学语法范围内。')
  const compiled = expression.compile()
  return (x: number) => {
    try {
      const result = compiled.evaluate({ x })
      return typeof result === 'number' && Number.isFinite(result) ? result : null
    } catch {
      return null
    }
  }
}

function number(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
}

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string): HTMLElementTagNameMap[K] {
  const item = document.createElement(tag)
  if (className) item.className = className
  return item
}

function createBoard(host: HTMLElement, boundingBox: [number, number, number, number]): { board: Board; plot: HTMLDivElement } {
  const plot = element('div', 'interactive-component-plot')
  plot.setAttribute('aria-hidden', 'true')
  host.append(plot)
  const board = JXG.JSXGraph.initBoard(plot, {
    boundingbox: boundingBox,
    axis: false,
    showCopyright: false,
    showNavigation: false,
    keepaspectratio: false,
    pan: { enabled: false },
    zoom: false,
  })
  return { board, plot }
}

function createControls(
  host: HTMLElement,
  controls: ComponentControlSpec[],
  onChange: (values: Map<string, boolean | number>) => void,
): Map<string, boolean | number> {
  const values = new Map<string, boolean | number>()
  if (!controls.length) return values
  const fieldset = element('fieldset', 'interactive-component-controls')
  const legend = element('legend')
  legend.textContent = '观察'
  fieldset.append(legend)
  controls.forEach((control) => {
    const label = element('label', 'interactive-component-control')
    const input = document.createElement('input')
    input.dataset.componentControl = control.control_id
    if (control.kind === 'toggle') {
      input.type = 'checkbox'
      input.checked = control.default_value
      values.set(control.control_id, input.checked)
      input.addEventListener('change', () => {
        values.set(control.control_id, input.checked)
        onChange(values)
      })
    } else {
      input.type = 'range'
      input.min = String(control.minimum)
      input.max = String(control.maximum)
      input.step = String(control.step)
      input.value = String(control.default_value)
      values.set(control.control_id, Number(input.value))
      input.addEventListener('input', () => {
        values.set(control.control_id, Number(input.value))
        onChange(values)
      })
    }
    const text = element('span')
    text.textContent = control.label
    label.append(input, text)
    fieldset.append(label)
  })
  host.append(fieldset)
  return values
}

function appendAnnotations(host: HTMLElement, spec: InteractiveComponentSpec): void {
  if (!spec.annotations.length) return
  const list = element('ul', 'interactive-component-annotations')
  spec.annotations.forEach((annotation) => {
    const item = element('li')
    item.textContent = annotation.text
    list.append(item)
  })
  host.append(list)
}

function endpoint(board: Board, x: number, y: number, inclusion: 'open' | 'closed', color = '#24334e'): any {
  return board.create('point', [x, y], {
    name: '',
    size: 3.5,
    face: 'o',
    strokeColor: color,
    fillColor: color,
    fillOpacity: inclusion === 'open' ? 0 : 1,
    fixed: true,
    highlight: false,
  })
}

function renderInterval(board: Board, spec: IntervalLineSpec, values: Map<string, boolean | number>, observation: HTMLElement): void {
  const span = spec.interval_end - spec.interval_start
  const y = 0
  board.create('axis', [[spec.interval_start - span * 0.18, y], [spec.interval_end + span * 0.18, y]], {
    withLabel: false,
    strokeColor: '#77746c',
    fixed: true,
    highlight: false,
  })
  board.create('segment', [[spec.interval_start, y], [spec.interval_end, y]], {
    strokeColor: '#b03a2e',
    strokeWidth: 4,
    fixed: true,
    highlight: false,
  })
  endpoint(board, spec.interval_start, y, spec.left_endpoint, '#b03a2e')
  endpoint(board, spec.interval_end, y, spec.right_endpoint, '#b03a2e')
  const marker = board.create('point', [spec.supremum, 0.42], {
    name: '',
    size: 3,
    face: 'diamond',
    strokeColor: '#0d6b63',
    fillColor: '#0d6b63',
    fixed: true,
    highlight: false,
    visible: values.get('show-supremum') === true,
  })
  const showingSupremum = values.get('show-supremum') === true
  marker.setAttribute({ visible: showingSupremum })
  observation.textContent = showingSupremum
    ? `上确界是 ${number(spec.supremum)}。端点开放，所以没有最大元。`
    : spec.accessibility.observation
}

function renderComplexPlane(board: Board, spec: ComplexPlaneSpec): void {
  const extent = Math.max(2, ...spec.points.flatMap((point) => [Math.abs(point.real), Math.abs(point.imaginary)])) + 1
  board.setBoundingBox([-extent, extent, extent, -extent], false)
  board.create('axis', [[-extent, 0], [extent, 0]], { withLabel: false, strokeColor: '#77746c', fixed: true, highlight: false })
  board.create('axis', [[0, -extent], [0, extent]], { withLabel: false, strokeColor: '#77746c', fixed: true, highlight: false })
  spec.points.forEach((point) => {
    board.create('point', [point.real, point.imaginary], {
      name: '', size: 3.5, face: 'o', strokeColor: '#0d6b63', fillColor: '#0d6b63', fixed: true, highlight: false,
    })
  })
}

function functionSegments(spec: FunctionGraphSpec, evaluate: (x: number) => number | null): Array<Array<[number, number]>> {
  const cuts = [spec.domain.start, ...spec.excluded_points.filter((point) => point > spec.domain.start && point < spec.domain.end).sort((a, b) => a - b), spec.domain.end]
  const epsilon = (spec.domain.end - spec.domain.start) / (spec.sample_count * 100)
  const segments: Array<Array<[number, number]>> = []
  for (let index = 0; index < cuts.length - 1; index += 1) {
    const leftExcluded = index > 0
    const rightExcluded = index < cuts.length - 2
    const start = cuts[index] + (leftExcluded ? epsilon : 0)
    const end = cuts[index + 1] - (rightExcluded ? epsilon : 0)
    if (start >= end) continue
    const points: Array<[number, number]> = []
    for (let sample = 0; sample <= spec.sample_count; sample += 1) {
      const x = start + (end - start) * sample / spec.sample_count
      const y = evaluate(x)
      if (y !== null) points.push([x, y])
    }
    if (points.length >= 2) segments.push(points)
  }
  return segments
}

function renderFunctionGraph(board: Board, spec: FunctionGraphSpec): void {
  const evaluate = compileRestrictedExpression(spec.formula)
  const segments = functionSegments(spec, evaluate)
  const values = segments.flatMap((segment) => segment.map((point) => point[1]))
  if (!values.length) throw new Error('函数在声明定义域内没有可绘制的有限值。')
  const yMin = Math.min(0, ...values)
  const yMax = Math.max(0, ...values)
  const ySpan = Math.max(1, yMax - yMin)
  const xSpan = spec.domain.end - spec.domain.start
  board.setBoundingBox([
    spec.domain.start - xSpan * 0.08,
    yMax + ySpan * 0.18,
    spec.domain.end + xSpan * 0.08,
    yMin - ySpan * 0.18,
  ], false)
  board.create('axis', [[spec.domain.start - xSpan * 0.08, 0], [spec.domain.end + xSpan * 0.08, 0]], { withLabel: false, strokeColor: '#77746c', fixed: true, highlight: false })
  board.create('axis', [[0, yMin - ySpan * 0.18], [0, yMax + ySpan * 0.18]], { withLabel: false, strokeColor: '#77746c', fixed: true, highlight: false })
  segments.forEach((segment) => {
    board.create('curve', [segment.map((point) => point[0]), segment.map((point) => point[1])], {
      strokeColor: '#0d6b63', strokeWidth: 2.5, fixed: true, highlight: false,
    })
  })
  const startY = evaluate(spec.domain.start)
  if (startY !== null && !spec.excluded_points.includes(spec.domain.start)) endpoint(board, spec.domain.start, startY, spec.domain.start_endpoint, '#0d6b63')
  const endY = evaluate(spec.domain.end)
  if (endY !== null && !spec.excluded_points.includes(spec.domain.end)) endpoint(board, spec.domain.end, endY, spec.domain.end_endpoint, '#0d6b63')
  const plot = board.containerObj as HTMLElement
  plot.dataset.curveSegments = String(segments.length)
  plot.dataset.excludedPoints = spec.excluded_points.map(number).join(',')
}

function renderBoard(host: HTMLElement, spec: InteractiveComponentSpec, values: Map<string, boolean | number>, observation: HTMLElement): Board {
  const provisional: [number, number, number, number] = spec.component_type === 'interval_line'
    ? [spec.interval_start - 0.3, 1.05, spec.interval_end + 0.3, -1.05]
    : spec.component_type === 'function_graph'
      ? [spec.domain.start - 0.2, 2, spec.domain.end + 0.2, -2]
      : [-3, 3, 3, -3]
  const { board } = createBoard(host, provisional)
  if (spec.component_type === 'interval_line') renderInterval(board, spec, values, observation)
  if (spec.component_type === 'complex_plane') renderComplexPlane(board, spec)
  if (spec.component_type === 'function_graph') renderFunctionGraph(board, spec)
  return board
}

export function renderInteractiveComponent(host: HTMLElement, spec: InteractiveComponentSpec): InteractiveComponentRenderHandle {
  if (!isInteractiveComponentSpec(spec)) throw new Error('交互组件规格不符合受控 IR 契约。')
  host.replaceChildren()
  host.classList.add('interactive-component-host')
  host.dataset.componentType = spec.component_type
  host.dataset.componentReady = 'false'
  host.setAttribute('aria-label', spec.accessibility.aria_label)

  const heading = element('div', 'interactive-component-heading')
  const eyebrow = element('p')
  eyebrow.textContent = '观察'
  const title = element('h4')
  title.textContent = spec.title
  const goal = element('p', 'interactive-component-goal')
  goal.textContent = spec.learning_objective
  heading.append(eyebrow, title, goal)
  host.append(heading)

  const boardHost = element('div', 'interactive-component-board')
  host.append(boardHost)
  const observation = element('p', 'interactive-component-observation')
  observation.id = 'interactive-observation'
  observation.setAttribute('aria-live', 'polite')
  observation.textContent = spec.accessibility.observation
  const description = element('p', 'interactive-component-description')
  description.textContent = spec.accessibility.description

  let board: Board | null = null
  const redraw = (values: Map<string, boolean | number>) => {
    if (board) JXG.JSXGraph.freeBoard(board)
    boardHost.replaceChildren()
    board = renderBoard(boardHost, spec, values, observation)
  }
  const values = createControls(host, spec.controls, redraw)
  redraw(values)
  host.append(observation, description)
  appendAnnotations(host, spec)
  host.dataset.componentReady = 'true'

  return {
    destroy: () => {
      if (board) JXG.JSXGraph.freeBoard(board)
      board = null
      host.replaceChildren()
      host.dataset.componentReady = 'false'
    },
  }
}
