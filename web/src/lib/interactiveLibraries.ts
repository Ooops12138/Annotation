import type { ComponentLibrary } from './document'

interface LibraryRuntime { javascript: string; css?: string }

// Raw bundles are fetched by the host from its own build, then injected inline.
// The generated iframe never needs module imports or network permissions.
const loaders: Record<ComponentLibrary, () => Promise<LibraryRuntime>> = {
  jsxgraph: async () => {
    const [javascript, css] = await Promise.all([
      import('../../node_modules/jsxgraph/distrib/jsxgraphcore.js?raw'),
      import('../../node_modules/jsxgraph/distrib/jsxgraph.css?raw'),
    ])
    return { javascript: javascript.default, css: css.default }
  },
  echarts: async () => ({ javascript: (await import('../../node_modules/echarts/dist/echarts.min.js?raw')).default }),
  d3: async () => ({ javascript: (await import('../../node_modules/d3/dist/d3.min.js?raw')).default }),
}

export async function componentLibraryMarkup(libraries: ComponentLibrary[]): Promise<string> {
  const runtimes = await Promise.all(libraries.map((library) => loaders[library]()))
  return runtimes.map(({ javascript, css }) => {
    const script = javascript.replace(/<\/script/gi, '<\\/script')
    const style = css ? `<style>${css.replace(/<\/style/gi, '<\\/style')}</style>` : ''
    return `${style}<script>${script}</script>`
  }).join('')
}
