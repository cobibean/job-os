import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { createMainCareerProfileClient } from './careerProfile.js'

type CareerProfileClient = ReturnType<typeof createMainCareerProfileClient>

export function registerCareerProfileIpc(
  ipc: Pick<IpcMain, 'handle'>,
  trusted: (event: IpcMainInvokeEvent) => CareerProfileClient
): void {
  ipc.handle('jobos:career-profile:availability', event => trusted(event).availability())
  ipc.handle('jobos:career-profile:cache:validate', (event, candidate) => trusted(event).validateCachedWorkArrangement(candidate))
  ipc.handle('jobos:career-profile:work-arrangement:get', event => trusted(event).getWorkArrangement())
  ipc.handle('jobos:career-profile:work-arrangement:save', (event, request) => trusted(event).saveWorkArrangement(request))
  ipc.handle('jobos:career-profile:work-arrangement:history', event => trusted(event).getWorkArrangementHistory())
  ipc.handle('jobos:career-profile:work-arrangement:restore', (event, request) => trusted(event).restoreWorkArrangement(request))
  ipc.handle('jobos:career-profile:agents:list', event => trusted(event).listConnectedAgents())
  ipc.handle('jobos:career-profile:agents:trust-mode', (event, agentId, trustMode) => trusted(event).updateConnectedAgentTrustMode(agentId, trustMode))
  ipc.handle('jobos:career-profile:agents:disconnect', (event, agentId) => trusted(event).disconnectConnectedAgent(agentId))
  ipc.handle('jobos:career-profile:proposals:list', event => trusted(event).listCareerProfileProposals())
  ipc.handle('jobos:career-profile:proposals:decide', (event, proposalId, request) => trusted(event).decideCareerProfileProposal(proposalId, request))
  ipc.handle('jobos:career-profile:history:get', event => trusted(event).getCareerProfileChangeHistory())
  ipc.handle('jobos:career-profile:history:undo', (event, revisionId, request) => trusted(event).undoCareerProfileChange(revisionId, request))
  ipc.handle('jobos:career-profile:get', event => trusted(event).getCareerProfile())
  ipc.handle('jobos:career-profile:items:create', (event, request) => trusted(event).createCareerProfileItem(request))
  ipc.handle('jobos:career-profile:items:update', (event, itemId, request) => trusted(event).updateCareerProfileItem(itemId, request))
  ipc.handle('jobos:career-profile:items:remove', (event, itemId, request) => trusted(event).removeCareerProfileItem(itemId, request))
  ipc.handle('jobos:career-profile:evidence:import', (event, request) => trusted(event).importCareerProfileEvidence(request))
  ipc.handle('jobos:career-profile:evidence:remove', (event, evidenceId, request) => trusted(event).removeCareerProfileEvidence(evidenceId, request))
  ipc.handle('jobos:career-profile:context:get', (event, agentId) => trusted(event).getCareerProfileContext(agentId))
  ipc.handle('jobos:career-profile:context:update', (event, agentId, request) => trusted(event).updateCareerProfileContext(agentId, request))
  ipc.handle('jobos:career-profile:context:preview', (event, agentId) => trusted(event).previewCareerProfileContext(agentId))
  ipc.handle('jobos:career-profile:export', (event, request) => trusted(event).exportCareerProfile(request))
  ipc.handle('jobos:career-profile:restore:choose', event => trusted(event).chooseCareerProfileArchive())
  ipc.handle('jobos:career-profile:restore', (event, request) => trusted(event).restoreCareerProfile(request))
}
