// This file is part of JobOS's modified GenOffice-derived package; see this package's UPSTREAM.md.
import { isPillPreset, presetPath, presetPolygon } from './preset-geometry.js'
export { WORDART_PRESETS, wordArtStrokePx, wordArtSolidColor } from './wordart-presets.js'

const round = (value: number) => Math.round(value * 100) / 100

export function shapeClipCss(prst: string, widthPx = 100, heightPx = 100): { clipPath?: string; borderRadius?: string } | undefined {
  if (prst === 'rect') return undefined
  if (prst === 'roundRect') return { borderRadius: '12%' }
  if (prst === 'ellipse') return { borderRadius: '50%' }
  if (isPillPreset(prst)) return { borderRadius: '9999px' }
  const polygon = presetPolygon(prst, 100, 100)
  if (polygon) {
    const points: string[] = []
    for (let index = 0; index < polygon.length; index += 2) points.push(`${round(polygon[index]!)}% ${round(polygon[index + 1]!)}%`)
    return { clipPath: `polygon(${points.join(', ')})` }
  }
  const path = presetPath(prst, widthPx, heightPx)
  const data = path?.fillPath ?? path?.path
  return data ? { clipPath: `path('${data}')` } : undefined
}
