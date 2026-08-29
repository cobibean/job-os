import { ArchiveRestore, Download, History, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { CareerProfileArea, CareerProfileBridge } from '../../../shared/contracts'
import { AgentAccessDialog } from './dialogs/AgentAccessDialog'
import { ExportDialog } from './dialogs/ExportDialog'
import { HistoryDialog } from './dialogs/HistoryDialog'
import { RestoreDialog } from './dialogs/RestoreDialog'
import { EvidenceArea } from './EvidenceArea'
import { ItemArea } from './ItemArea'
import type { CareerProfileProductController } from './useCareerProfileProduct'

interface CareerProfileProductExperienceProps {
  active: boolean
  activeArea: CareerProfileArea
  bridge: CareerProfileBridge
  hasActiveTurn: boolean
  onBaselineRestored: () => Promise<boolean>
  online: boolean
  product: CareerProfileProductController
}

export function CareerProfileProductExperience({
  active,
  activeArea,
  hasActiveTurn,
  onBaselineRestored,
  online,
  product
}: CareerProfileProductExperienceProps) {
  const [dialog, setDialog] = useState<'context' | 'export' | 'restore' | 'history' | null>(null)
  const writable = online && !product.readOnly
  const evidenceMessageHandledLocally = activeArea === 'my_evidence'
    && /(added to My Evidence|could not be imported|changed somewhere else)/.test(product.message)

  useEffect(() => { if (!active) setDialog(null) }, [active])

  return (
    <>
      <div className="career-product-toolbar" aria-label="Career Profile actions">
        <button className="career-secondary-button" onClick={() => setDialog('context')} type="button"><ShieldCheck aria-hidden="true" size={15} />Agent access</button>
        <button className="career-secondary-button" onClick={() => setDialog('history')} type="button"><History aria-hidden="true" size={15} />History</button>
        <button className="career-secondary-button" onClick={() => setDialog('export')} type="button"><Download aria-hidden="true" size={15} />Export</button>
        <button className="career-secondary-button" onClick={() => setDialog('restore')} type="button"><ArchiveRestore aria-hidden="true" size={15} />Restore baseline</button>
      </div>

      {product.status === 'loading' ? <div className="career-product-loading" role="status">Loading complete Career Profile…</div> : null}
      {product.status === 'error' && !product.current ? <div className="career-product-recover" role="alert"><p>{product.message}</p><button className="career-secondary-button" onClick={() => { void product.load() }} type="button">Retry complete profile</button></div> : null}
      {product.current && !online ? <p className="career-feedback career-product-message error" role="status">Offline — saved complete-profile content is still readable. Reconnect before changing, importing, exporting, or restoring it.</p> : null}
      {product.message && product.current && product.status !== 'saving' && !evidenceMessageHandledLocally ? <p className={`career-feedback career-product-message ${product.status}`} role={product.status === 'error' || product.status === 'conflict' ? 'alert' : 'status'}>{product.message}</p> : null}

      {product.current ? (
        <>
          <div hidden={!active || activeArea !== 'my_career'}><ItemArea active={active && activeArea === 'my_career'} area="my_career" online={writable} product={product} /></div>
          <div hidden={!active || activeArea !== 'what_im_looking_for'}><ItemArea active={active && activeArea === 'what_im_looking_for'} area="what_im_looking_for" online={writable} product={product} /></div>
          <div hidden={!active || activeArea !== 'my_evidence'}><EvidenceArea active={active && activeArea === 'my_evidence'} online={writable} product={product} /></div>
        </>
      ) : null}

      {dialog === 'context' ? <AgentAccessDialog onClose={() => setDialog(null)} online={writable} product={product} /> : null}
      {dialog === 'history' ? <HistoryDialog onClose={() => setDialog(null)} online={writable} product={product} /> : null}
      {dialog === 'export' ? <ExportDialog onClose={() => setDialog(null)} online={writable} product={product} /> : null}
      {dialog === 'restore' ? <RestoreDialog hasActiveTurn={hasActiveTurn} onClose={() => setDialog(null)} onRestored={onBaselineRestored} online={writable} product={product} /> : null}
    </>
  )
}
