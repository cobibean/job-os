import { BriefcaseBusiness, MapPin, Sparkles } from 'lucide-react'
import { useCallback, useState } from 'react'

import type { CareerProfileArea, CareerProfileBridge } from '../../shared/contracts'
import { CollaborationArea } from './collaboration/CollaborationArea'
import { useCareerProfileCollaboration } from './collaboration/useCareerProfileCollaboration'
import { CareerProfileProductExperience } from './product/CareerProfileProductExperience'
import { useCareerProfileProduct } from './product/useCareerProfileProduct'
import { WorkArrangementArea } from './work-arrangement/WorkArrangementArea'
import { useCareerProfile } from './work-arrangement/useCareerProfile'

interface CareerProfileWorkspaceProps {
  active?: boolean
  bridge?: CareerProfileBridge
  hasActiveTurn: boolean
  online?: boolean
}

const sectionCopy: Record<CareerProfileArea, { breadcrumb: string; description: string; title: string }> = {
  my_career: {
    breadcrumb: 'My Career',
    description: 'Keep the experience, skills, education, projects, and positioning you want JobOS to remember.',
    title: 'My Career'
  },
  what_im_looking_for: {
    breadcrumb: 'What I’m Looking For',
    description: 'Tell JobOS what you want next and how firmly it should apply each choice.',
    title: 'Work arrangement'
  },
  my_evidence: {
    breadcrumb: 'My Evidence',
    description: 'Keep the source files that support your story, with clear provenance and independent import recovery.',
    title: 'My Evidence'
  }
}

export function CareerProfileWorkspace({ active = true, bridge = window.jobos.careerProfile, hasActiveTurn, online = true }: CareerProfileWorkspaceProps) {
  const profile = useCareerProfile(bridge)
  const product = useCareerProfileProduct(bridge)
  const refreshProfile = useCallback(async () => {
    const [, completeProfileRefreshed] = await Promise.all([profile.load(false), product.load(false)])
    return completeProfileRefreshed
  }, [product.load, profile.load])
  const collaboration = useCareerProfileCollaboration(bridge, online, refreshProfile)
  const [activeArea, setActiveArea] = useState<CareerProfileArea>('what_im_looking_for')

  if (profile.status === 'loading') {
    return (
      <main aria-busy="true" aria-label="Career Profile" className="career-profile-workspace">
        <aside className="career-profile-rail"><div className="career-skeleton rail" /></aside>
        <section className="career-profile-main"><div className="career-skeleton title" /><div className="career-skeleton card" role="status">Loading Career Profile…</div></section>
      </main>
    )
  }

  if (!profile.current && profile.status === 'error') {
    return (
      <main className="career-profile-workspace career-profile-centered">
        <section className="career-state-card" role="alert">
          <Sparkles aria-hidden="true" size={22} />
          <h1>Career Profile is unavailable right now</h1>
          <p>Your existing JobOS work is safe. Check the JobOS service connection and try again.</p>
          <button className="career-secondary-button" onClick={() => { void profile.load() }} type="button">Try again</button>
        </section>
      </main>
    )
  }

  const currentValue = profile.current?.record?.value
  const productItems = product.current?.items ?? []
  const myCareerCount = productItems.filter(item => item.area === 'my_career').length
  const lookingCount = productItems.filter(item => item.area === 'what_im_looking_for').length + (currentValue ? 1 : 0)
  const evidenceCount = product.current?.sourceEvidence.length ?? 0
  const visibleSection = sectionCopy[activeArea]

  return (
    <main className="career-profile-workspace">
      <aside aria-label="Career Profile sections" className="career-profile-rail">
        <div className="career-profile-heading">
          <span className="career-eyebrow">Your shared context</span>
          <h1>Career Profile</h1>
          <p>The information JobOS uses to understand your career and preferences.</p>
        </div>
        <nav className="career-profile-nav">
          <button aria-current={activeArea === 'my_career' ? 'page' : undefined} className={`career-nav-item ${activeArea === 'my_career' ? 'active' : ''}`} onClick={() => setActiveArea('my_career')} type="button"><BriefcaseBusiness aria-hidden="true" size={17} /><span><strong>My Career</strong><small>{myCareerCount} detail{myCareerCount === 1 ? '' : 's'}</small></span></button>
          <button aria-current={activeArea === 'what_im_looking_for' ? 'page' : undefined} className={`career-nav-item ${activeArea === 'what_im_looking_for' ? 'active' : ''}`} onClick={() => setActiveArea('what_im_looking_for')} type="button"><MapPin aria-hidden="true" size={17} /><span><strong>What I’m Looking For</strong><small>{lookingCount} preference{lookingCount === 1 ? '' : 's'}</small></span></button>
          <button aria-current={activeArea === 'my_evidence' ? 'page' : undefined} className={`career-nav-item ${activeArea === 'my_evidence' ? 'active' : ''}`} onClick={() => setActiveArea('my_evidence')} type="button"><Sparkles aria-hidden="true" size={17} /><span><strong>My Evidence</strong><small>{evidenceCount} source{evidenceCount === 1 ? '' : 's'}</small></span></button>
        </nav>
        <div className="career-staging-note"><span>JobOS Career Profile</span><p>This is the shared context JobOS and connected agents use.</p></div>
      </aside>

      <section className="career-profile-main">
        <span className="career-mobile-staging">JobOS Career Profile</span>
        <label className="career-mobile-nav">
          <span>Profile section</span>
          <select aria-label="Career Profile section" onChange={event => setActiveArea(event.target.value as CareerProfileArea)} value={activeArea}>
            <option value="my_career">My Career</option>
            <option value="what_im_looking_for">What I’m Looking For</option>
            <option value="my_evidence">My Evidence</option>
          </select>
        </label>
        <header className="career-detail-header">
          <div>
            <span className="career-breadcrumb">{visibleSection.breadcrumb}</span>
            <h2>{visibleSection.title}</h2>
            <p>{visibleSection.description}</p>
          </div>
          {profile.current?.record ? <span className="career-revision-badge">Revision {profile.current.profileRevision}</span> : null}
        </header>

        <CollaborationArea collaboration={collaboration} online={online} />

        <CareerProfileProductExperience
          active={active}
          activeArea={activeArea}
          bridge={bridge}
          hasActiveTurn={hasActiveTurn}
          onBaselineRestored={refreshProfile}
          online={online}
          product={product}
        />

        <WorkArrangementArea
          active={active && activeArea === 'what_im_looking_for'}
          hasActiveTurn={hasActiveTurn}
          online={online}
          profile={profile}
        />
      </section>
    </main>
  )
}
