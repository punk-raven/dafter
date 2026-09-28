import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

export function load(...scripts) {
  const context = vm.createContext({});
  for (const script of scripts) {
    const path = fileURLToPath(new URL(`../${script}`, import.meta.url));
    vm.runInContext(readFileSync(path, 'utf8'), context, { filename: script });
  }
  return (expression) => vm.runInContext(expression, context);
}

export function room(agentName) {
  const agent = { identity: 'agent-AJ_7f3a9c21', isAgent: true, name: agentName };
  const peer = { identity: 'p_9c2e11aa', isAgent: false, name: '' };
  return {
    localParticipant: { identity: 'p_4b81e0d7' },
    remoteParticipants: new Map([[agent.identity, agent], [peer.identity, peer]]),
  };
}
