(() => {
  const root = document.querySelector('[data-knowledge-graph]');
  if (!root) return;

  const sourceInput = root.querySelector('[data-graph-source]');
  const searchInput = root.querySelector('[data-graph-search]');
  const edgeLabelsInput = root.querySelector('[data-graph-edge-labels]');
  const orphansInput = root.querySelector('[data-graph-orphans]');
  const canvas = root.querySelector('[data_uniq="ai_base_knowledge_graph_canvas"]');
  const loading = root.querySelector('[data_uniq="ai_base_knowledge_graph_loading"]');
  const status = root.querySelector('[data-graph-status]');
  const results = root.querySelector('[data-graph-results]');
  const detail = root.querySelector('[data-graph-detail]');
  const state = { graph: { nodes: [], edges: [] }, overview: null };

  const request = async (url, params = {}) => {
    const target = new URL(url, window.location.origin);
    Object.entries(params).forEach(([key, value]) => value && target.searchParams.set(key, value));
    const response = await fetch(target, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    if (!response.ok) throw new Error('Knowledge graph request failed.');
    return response.json();
  };
  const clear = element => { while (element.firstChild) element.removeChild(element.firstChild); };
  const short = value => String(value || '').slice(0, 44);
  const nodeMap = () => new Map(state.graph.nodes.map(node => [node.id, node]));
  const connectedIds = () => new Set(state.graph.edges.flatMap(edge => [edge.source, edge.target]));

  const selectNode = async node => {
    detail.textContent = 'Loading relationships…';
    try {
      const data = await request(root.dataset.neighborsUrl, { node_id: node.id, depth: 1, limit: 50 });
      const neighborNames = (data.neighbors || []).map(item => item.label).join(', ') || 'No visible relationships.';
      detail.textContent = `${node.label}: ${node.description || 'No description.'} Neighbors: ${neighborNames}`;
    } catch (_error) {
      detail.textContent = `${node.label}: ${node.description || 'No description.'}`;
    }
  };

  const render = () => {
    clear(canvas);
    const connected = connectedIds();
    const visibleNodes = state.graph.nodes.filter(node => orphansInput.checked || !connected.size || connected.has(node.id));
    const visibleIds = new Set(visibleNodes.map(node => node.id));
    const visibleEdges = state.graph.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target));
    const layers = { company: 0, policy: 1, rule: 2, document: 3, process: 4, step: 5 };
    const grouped = [[], [], [], [], [], []];
    visibleNodes.forEach(node => grouped[layers[node.type] ?? 4].push(node));
    const height = Math.max(360, ...grouped.map(group => group.length * 74 + 72));
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 1320 ${height}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Company knowledge graph');
    const positions = new Map();
    grouped.forEach((group, layer) => group.forEach((node, index) => positions.set(node.id, { x: 85 + layer * 210, y: 56 + index * 74 })));
    visibleEdges.forEach(edge => {
      const source = positions.get(edge.source); const target = positions.get(edge.target);
      if (!source || !target) return;
      const line = document.createElementNS(svg.namespaceURI, 'line');
      line.setAttribute('x1', source.x + 70); line.setAttribute('y1', source.y + 17);
      line.setAttribute('x2', target.x - 12); line.setAttribute('y2', target.y + 17);
      line.setAttribute('class', 'hashy-graph-edge'); svg.appendChild(line);
      if (edgeLabelsInput.checked) { const label = document.createElementNS(svg.namespaceURI, 'text'); label.setAttribute('x', (source.x + target.x) / 2); label.setAttribute('y', (source.y + target.y) / 2); label.setAttribute('class', 'hashy-graph-edge-label'); label.textContent = edge.label; svg.appendChild(label); }
    });
    visibleNodes.forEach(node => {
      const position = positions.get(node.id); const group = document.createElementNS(svg.namespaceURI, 'g'); group.setAttribute('class', `hashy-graph-node type-${node.type}`); group.setAttribute('tabindex', '0'); group.setAttribute('role', 'button'); group.setAttribute('aria-label', node.label); const rect = document.createElementNS(svg.namespaceURI, 'rect'); rect.setAttribute('x', position.x); rect.setAttribute('y', position.y); rect.setAttribute('width', '140'); rect.setAttribute('height', '34'); rect.setAttribute('rx', '8'); const label = document.createElementNS(svg.namespaceURI, 'text'); label.setAttribute('x', position.x + 8); label.setAttribute('y', position.y + 21); label.textContent = short(node.label); group.append(rect, label); group.addEventListener('click', () => selectNode(node)); group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(node); } }); svg.appendChild(group);
    });
    canvas.appendChild(svg);
    status.textContent = `${visibleNodes.length} visible nodes · ${visibleEdges.length} relationships${state.graph.meta?.truncated ? ' · bounded view' : ''}`;
  };

  const load = async () => {
    loading.textContent = 'Loading knowledge graph…';
    try {
      const [overview, payload] = await Promise.all([request(root.dataset.overviewUrl), request(root.dataset.viewUrl, { source: sourceInput.value, limit: 50 })]);
      state.overview = overview; state.graph = { ...payload.graph, meta: payload.meta }; render();
      loading.textContent = `Graph ready · ${overview.counts.documents} documents · ${overview.counts.flows} business flows`;
    } catch (_error) { loading.textContent = 'Knowledge graph could not be loaded.'; status.textContent = 'Refresh the page or verify Assistant administrator access.'; }
  };

  const search = async () => {
    const query = searchInput.value.trim(); clear(results); if (!query) return;
    try { const payload = await request(root.dataset.searchUrl, { q: query, source: sourceInput.value, limit: 20 }); (payload.results || []).forEach(node => { const item = document.createElement('li'); const button = document.createElement('button'); button.type = 'button'; button.textContent = `${node.label} · ${node.type}`; button.addEventListener('click', () => selectNode(node)); item.appendChild(button); results.appendChild(item); }); } catch (_error) { results.textContent = 'Search unavailable.'; }
  };

  sourceInput.addEventListener('change', load); edgeLabelsInput.addEventListener('change', render); orphansInput.addEventListener('change', render); searchInput.addEventListener('input', () => { window.clearTimeout(searchInput._graphTimer); searchInput._graphTimer = window.setTimeout(search, 180); }); load();
})();
