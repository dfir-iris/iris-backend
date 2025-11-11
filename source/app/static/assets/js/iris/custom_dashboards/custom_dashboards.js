(function ($) {
  const apiBase = '/custom-dashboards/api/dashboards';
  const DEFAULT_RANGE_DAYS = 30;
  const DASHBOARD_AUTO_REFRESH_INTERVAL_MS = 60000;
  const DASHBOARD_STATE_STORAGE_KEY = 'iris.customDashboards.state';
  const CHART_COLORS = [
    '#4e73df', '#1cc88a', '#36b9cc', '#f6c23e', '#e74a3b', '#858796', '#fd7e14', '#20c997', '#6f42c1', '#17a2b8'
  ];

  let dashboardState = loadDashboardState();
  let currentDashboardId = dashboardState && dashboardState.lastDashboardId !== undefined && dashboardState.lastDashboardId !== null
    ? Number(dashboardState.lastDashboardId)
    : null;
  let dashboardsCache = [];
  let chartInstances = {};
  let dashboardAutoRefreshTimer = null;
  let isFetchingDashboardData = false;
  let currentQuickRangeKey = null;
  let canShareDashboards = false;
  let dashboardJsonEditor = null;

  function loadDashboardState() {
    try {
      const raw = window.localStorage ? window.localStorage.getItem(DASHBOARD_STATE_STORAGE_KEY) : null;
      if (!raw) {
        return {};
      }
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (err) {
      console.warn('Failed to load dashboard state from storage', err);
      return {};
    }
  }

  function persistDashboardState(state) {
    try {
      if (!window.localStorage) {
        return;
      }
      window.localStorage.setItem(DASHBOARD_STATE_STORAGE_KEY, JSON.stringify(state || {}));
    } catch (err) {
      console.warn('Failed to persist dashboard state', err);
    }
  }

  function updateDashboardState(patch) {
    const nextState = Object.assign({}, dashboardState || {}, patch || {});
    dashboardState = nextState;
    persistDashboardState(nextState);
  }

  function buildApiUrl(path, params) {
    const trimmedPath = (path || '').trim();
    if (!params) {
      return trimmedPath;
    }
    const queryPairs = Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== '');
    if (!queryPairs.length) {
      return trimmedPath;
    }
    const queryString = queryPairs.map(([key, value]) => {
      const normalizedValue = value instanceof Date ? value.toISOString() : value;
      return `${encodeURIComponent(key)}=${encodeURIComponent(normalizedValue)}`;
    }).join('&');
    const separator = trimmedPath.indexOf('?') === -1 ? '?' : '&';
    return `${trimmedPath}${separator}${queryString}`;
  }

  function parseInputDate(value) {
    if (!value) {
      return null;
    }
    if (value instanceof Date) {
      return Number.isNaN(value.getTime()) ? null : value;
    }
    if (typeof value !== 'string') {
      return null;
    }
    const trimmed = value.trim();
    if (!trimmed) {
      return null;
    }
    const normalized = trimmed.replace(' ', 'T');
    let candidate = new Date(normalized);
    if (Number.isNaN(candidate.getTime())) {
      candidate = new Date(`${normalized}:00`);
    }
    return Number.isNaN(candidate.getTime()) ? null : candidate;
  }

  function pad(value) {
    return String(value).padStart(2, '0');
  }

  function formatDateForInput(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
      return '';
    }
    const local = new Date(date.getTime() - (date.getTimezoneOffset() * 60000));
    const year = local.getFullYear();
    const month = pad(local.getMonth() + 1);
    const day = pad(local.getDate());
    const hours = pad(local.getHours());
    const minutes = pad(local.getMinutes());
    return `${year}-${month}-${day}T${hours}:${minutes}`;
  }

  function getDashboardJsonEditor() {
    if (typeof ace === 'undefined') {
      return null;
    }

    if (dashboardJsonEditor) {
      return dashboardJsonEditor;
    }

    const container = document.getElementById('dashboardJsonEditor');
    if (!container) {
      return null;
    }

    dashboardJsonEditor = ace.edit(container, {
      autoScrollEditorIntoView: true,
      highlightActiveLine: true,
      minLines: 18,
      maxLines: Infinity
    });
    dashboardJsonEditor.setTheme('ace/theme/tomorrow');
    try {
      dashboardJsonEditor.session.setMode('ace/mode/json');
      dashboardJsonEditor.session.setUseWorker(false);
    } catch (err) {
      console.warn('Unable to set Ace editor mode', err);
    }
    dashboardJsonEditor.session.setUseWrapMode(true);
    dashboardJsonEditor.setOption('showPrintMargin', false);
    dashboardJsonEditor.setOption('showLineNumbers', true);
    dashboardJsonEditor.setOption('tabSize', 2);
    dashboardJsonEditor.setOption('useSoftTabs', true);
    dashboardJsonEditor.renderer.setShowGutter(true);
    dashboardJsonEditor.renderer.setScrollMargin(8, 8);

    const textarea = $('#dashboardJsonTextarea');
    if (textarea.length) {
      dashboardJsonEditor.session.on('change', function () {
        textarea.val(dashboardJsonEditor.getValue());
      });
      textarea.addClass('d-none');
      const initialValue = textarea.val() || '';
      dashboardJsonEditor.setValue(initialValue, -1);
      dashboardJsonEditor.clearSelection();
    }

    $('#dashboardJsonEditor').removeClass('d-none');

    return dashboardJsonEditor;
  }

  function setDashboardJsonContent(content) {
    const normalized = content || '';
    const textarea = $('#dashboardJsonTextarea');
    textarea.val(normalized);
    const editor = getDashboardJsonEditor();
    if (editor) {
      editor.setValue(normalized, -1);
      editor.clearSelection();
    }
  }

  function getDashboardJsonContent() {
    const editor = getDashboardJsonEditor();
    if (editor) {
      return editor.getValue();
    }
    return $('#dashboardJsonTextarea').val() || '';
  }

  function focusDashboardJsonEditor() {
    const editor = getDashboardJsonEditor();
    if (editor) {
      editor.resize(true);
      editor.focus();
      return;
    }
    const textarea = $('#dashboardJsonTextarea');
    if (textarea.length) {
      textarea.removeClass('d-none').focus();
    }
  }

  function formatDateForDisplay(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
      return '--';
    }
    return date.toLocaleString();
  }
  function renderTableWidget(widget, container, widgetOptionsOverride) {
    const data = widget.data || {};
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const groupHeaders = Array.isArray(data.group_headers) ? data.group_headers : [];
    const valueHeaders = Array.isArray(data.value_headers) ? data.value_headers : [];
    const valueKeys = Array.isArray(data.value_keys) ? data.value_keys : [];
    const totalLabel = data.total_label || 'Total';
    const widgetOptions = widgetOptionsOverride || (widget.definition && widget.definition.options) || widget.options || {};
    const displayMode = normalizeDisplayMode(widgetOptions.display_mode || widgetOptions.display || data.display_mode);

    const originalRows = rows.map((row) => ({
      group_values: Array.isArray(row.group_values) ? row.group_values.slice() : [],
      formatted_group_values: Array.isArray(row.formatted_group_values) ? row.formatted_group_values.slice() : [],
      value_cells: Array.isArray(row.value_cells) ? row.value_cells.map((cell) => Object.assign({}, cell)) : []
    }));

    if (!originalRows.length) {
      container.append('<div class="text-muted">No data available.</div>');
      return;
    }

    const controls = $('<div class="table-controls d-flex flex-wrap align-items-center mb-2"></div>');
    const searchInput = $('<input type="search" class="form-control form-control-sm table-search-input mr-2 mb-2" placeholder="Search table...">');
    controls.append(searchInput);
    container.append(controls);

    const tableWrapper = $('<div class="custom-dashboard-table table-responsive"></div>');
    const table = $('<table class="table table-sm table-striped mb-0"></table>');
    const thead = $('<thead></thead>');
    const headerRow = $('<tr></tr>');
    const sortableHeaders = [];

    if (groupHeaders.length) {
      groupHeaders.forEach((header, index) => {
        const th = $('<th></th>').text(header).addClass('table-sortable').data('sortKey', { type: 'group', index });
        th.append('<span class="sort-indicator"></span>');
        headerRow.append(th);
        sortableHeaders.push(th);
      });
    }

    const effectiveHeaders = (valueHeaders.length ? valueHeaders : valueKeys).slice();
    if (effectiveHeaders.length) {
      effectiveHeaders.forEach((header, index) => {
        const headerLabel = valueHeaders.length ? header : formatHeaderLabel(header || valueKeys[index] || `Value ${index + 1}`);
        if (displayMode === 'number_percentage') {
          const valueTh = $('<th class="text-right"></th>').text(`${headerLabel} (Value)`).addClass('table-sortable').data('sortKey', { type: 'value', index, mode: 'number' });
          valueTh.append('<span class="sort-indicator"></span>');
          const percentTh = $('<th class="text-right"></th>').text(`${headerLabel} (%)`).addClass('table-sortable').data('sortKey', { type: 'value', index, mode: 'percentage' });
          percentTh.append('<span class="sort-indicator"></span>');
          headerRow.append(valueTh, percentTh);
          sortableHeaders.push(valueTh, percentTh);
        } else if (displayMode === 'percentage') {
          const percentTh = $('<th class="text-right"></th>').text(`${headerLabel} (%)`).addClass('table-sortable').data('sortKey', { type: 'value', index, mode: 'percentage' });
          percentTh.append('<span class="sort-indicator"></span>');
          headerRow.append(percentTh);
          sortableHeaders.push(percentTh);
        } else {
          const valueTh = $('<th class="text-right"></th>').text(headerLabel).addClass('table-sortable').data('sortKey', { type: 'value', index, mode: 'number' });
          valueTh.append('<span class="sort-indicator"></span>');
          headerRow.append(valueTh);
          sortableHeaders.push(valueTh);
        }
      });
    } else if (!groupHeaders.length) {
      headerRow.append($('<th></th>').text('Value'));
    }

    thead.append(headerRow);
    table.append(thead);

    const tbody = $('<tbody></tbody>');
    const tfoot = $('<tfoot></tfoot>');
    table.append(tbody);
    table.append(tfoot);
    tableWrapper.append(table);
    container.append(tableWrapper);

    const state = {
      searchQuery: '',
      sort: null
    };

    function cloneRow(row) {
      return {
        group_values: row.group_values.slice(),
        formatted_group_values: row.formatted_group_values.slice(),
        value_cells: row.value_cells.map((cell) => Object.assign({}, cell))
      };
    }

    function matchesQuery(row, query) {
      if (!query) {
        return true;
      }
      const haystack = [];
      row.formatted_group_values.forEach((entry) => {
        if (entry !== null && entry !== undefined) {
          haystack.push(String(entry).toLowerCase());
        }
      });
      row.value_cells.forEach((cell) => {
        if (!cell) {
          return;
        }
        if (cell.formatted_value !== undefined && cell.formatted_value !== null) {
          haystack.push(String(cell.formatted_value).toLowerCase());
        }
        if (cell.formatted_percentage !== undefined && cell.formatted_percentage !== null) {
          haystack.push(String(cell.formatted_percentage).toLowerCase());
        }
      });
      return haystack.some((entry) => entry.indexOf(query) !== -1);
    }

    function compareValues(a, b) {
      if (a === b) {
        return 0;
      }
      if (a === null || a === undefined) {
        return 1;
      }
      if (b === null || b === undefined) {
        return -1;
      }
      if (typeof a === 'number' && typeof b === 'number') {
        return a - b;
      }
      return String(a).localeCompare(String(b));
    }

    function buildProcessedDataset() {
      const query = state.searchQuery.trim().toLowerCase();
      let processed = originalRows.map((row) => {
        const cloned = cloneRow(row);
        cloned.value_cells.forEach((cell) => {
          if (cell) {
            if (cell.formatted_value === undefined) {
              cell.formatted_value = formatNumberValue(cell.value);
            }
            if (cell.formatted_percentage === undefined) {
              cell.formatted_percentage = cell.percentage !== undefined ? formatPercentageValue(cell.percentage) : '--';
            }
          }
        });
        return cloned;
      });

      if (query) {
        processed = processed.filter((row) => matchesQuery(row, query));
      }

      if (state.sort && state.sort.type) {
        const sortConfig = state.sort;
        processed.sort((a, b) => {
          let aValue = null;
          let bValue = null;
          if (sortConfig.type === 'group') {
            const formattedA = a.formatted_group_values[sortConfig.index];
            const formattedB = b.formatted_group_values[sortConfig.index];
            aValue = formattedA !== undefined ? formattedA : a.group_values[sortConfig.index];
            bValue = formattedB !== undefined ? formattedB : b.group_values[sortConfig.index];
          } else if (sortConfig.type === 'value') {
            const cellA = a.value_cells[sortConfig.index];
            const cellB = b.value_cells[sortConfig.index];
            if (sortConfig.mode === 'percentage') {
              aValue = cellA ? toNumericValue(cellA.percentage) : null;
              bValue = cellB ? toNumericValue(cellB.percentage) : null;
            } else {
              aValue = cellA ? toNumericValue(cellA.value) : null;
              bValue = cellB ? toNumericValue(cellB.value) : null;
            }
          }
          const base = compareValues(aValue, bValue);
          return sortConfig.direction === 'desc' ? -base : base;
        });
      }

      const totals = (valueKeys.length ? valueKeys : processed[0].value_cells.map((_, idx) => idx)).map((key, index) => {
        let sum = null;
        processed.forEach((row) => {
          const cell = row.value_cells[index];
          const numeric = cell ? toNumericValue(cell.value) : null;
          if (numeric !== null) {
            sum = sum === null ? numeric : sum + numeric;
          }
        });
        return {
          key: key || valueKeys[index] || null,
          value: sum,
          formatted_value: formatNumberValue(sum),
          percentage: sum === null ? null : (sum === 0 ? 0 : 100),
          formatted_percentage: sum === null ? '--' : formatPercentageValue(sum === 0 ? 0 : 100)
        };
      });

      if (displayMode === 'percentage' || displayMode === 'number_percentage') {
        processed.forEach((row) => {
          row.value_cells.forEach((cell, index) => {
            if (!cell) {
              return;
            }
            const totalEntry = totals[index];
            const numeric = toNumericValue(cell.value);
            let percentage = null;
            if (numeric !== null && totalEntry && totalEntry.value !== null) {
              percentage = totalEntry.value === 0 ? 0 : (numeric / totalEntry.value) * 100;
            } else if (numeric !== null && totalEntry && totalEntry.value === 0) {
              percentage = 0;
            }
            cell.percentage = percentage;
            cell.formatted_percentage = percentage !== null ? formatPercentageValue(percentage) : '--';
          });
        });

        totals.forEach((entry) => {
          if (!entry) {
            return;
          }
          if (entry.value === null) {
            entry.percentage = null;
            entry.formatted_percentage = '--';
          } else if (entry.value === 0) {
            entry.percentage = 0;
            entry.formatted_percentage = formatPercentageValue(0);
          } else {
            entry.percentage = 100;
            entry.formatted_percentage = formatPercentageValue(100);
          }
        });
      }

      return { rows: processed, totals };
    }

    function updateSortIndicators() {
      sortableHeaders.forEach((header) => {
        header.removeClass('sort-asc sort-desc');
      });
      if (!state.sort || !state.sort.type) {
        return;
      }
      const target = sortableHeaders.find((header) => {
        const sortKey = header.data('sortKey');
        if (!sortKey || !state.sort) {
          return false;
        }
        if (sortKey.type !== state.sort.type) {
          return false;
        }
        if (sortKey.index !== state.sort.index) {
          return false;
        }
        if (sortKey.mode && state.sort.mode && sortKey.mode !== state.sort.mode) {
          return false;
        }
        return true;
      });
      if (target) {
        target.addClass(state.sort.direction === 'desc' ? 'sort-desc' : 'sort-asc');
      }
    }

    function renderBody() {
      const dataset = buildProcessedDataset();
      const processedRows = dataset.rows;
      const totals = dataset.totals;
      tbody.empty();

      processedRows.forEach((row) => {
        const tr = $('<tr></tr>');
        if (groupHeaders.length) {
          groupHeaders.forEach((_, index) => {
            const formattedValue = row.formatted_group_values[index];
            const rawValue = row.group_values[index];
            const candidate = formattedValue !== undefined ? formattedValue : rawValue;
            const textValue = candidate === null || candidate === undefined || candidate === '' ? 'N/A' : candidate;
            tr.append($('<td></td>').text(textValue));
          });
        }

        const appliedHeaders = effectiveHeaders.length ? effectiveHeaders : row.value_cells.map((_, index) => valueKeys[index] || index);
        if (appliedHeaders.length) {
          appliedHeaders.forEach((header, index) => {
            const cell = row.value_cells[index] || null;
            const numberText = cell && cell.formatted_value !== undefined ? cell.formatted_value : formatNumberValue(cell ? cell.value : null);
            const percentageText = cell && cell.formatted_percentage !== undefined ? cell.formatted_percentage : formatPercentageValue(cell ? cell.percentage : null);
            if (displayMode === 'number_percentage') {
              tr.append($('<td class="text-right"></td>').text(numberText));
              tr.append($('<td class="text-right"></td>').text(percentageText));
            } else if (displayMode === 'percentage') {
              tr.append($('<td class="text-right"></td>').text(percentageText));
            } else {
              tr.append($('<td class="text-right"></td>').text(numberText));
            }
          });
        } else {
          const firstCell = row.value_cells[0];
          const fallbackText = firstCell && firstCell.formatted_value !== undefined ? firstCell.formatted_value : formatNumberValue(firstCell ? firstCell.value : null);
          tr.append($('<td class="text-right"></td>').text(fallbackText));
        }
        tbody.append(tr);
      });

      tfoot.empty();
      const appliedHeaders = effectiveHeaders.length ? effectiveHeaders : valueKeys;
      const hasTotals = totals.some((entry) => entry && entry.value !== null && entry.value !== undefined);
      if (hasTotals && appliedHeaders.length) {
        const totalRow = $('<tr class="font-weight-bold"></tr>');
        if (groupHeaders.length) {
          totalRow.append($('<td></td>').attr('colspan', groupHeaders.length).text(totalLabel));
        }
        let firstValueCell = !groupHeaders.length;
        appliedHeaders.forEach((header, index) => {
          const entry = totals[index] || null;
          const numberText = entry && entry.formatted_value !== undefined ? entry.formatted_value : formatNumberValue(entry ? entry.value : null);
          const percentageText = entry && entry.formatted_percentage !== undefined ? entry.formatted_percentage : formatPercentageValue(entry ? entry.percentage : null);
          if (displayMode === 'number_percentage') {
            const numberCellText = firstValueCell ? `${totalLabel}: ${numberText}` : numberText;
            totalRow.append($('<td class="text-right"></td>').text(numberCellText));
            totalRow.append($('<td class="text-right"></td>').text(percentageText));
            firstValueCell = false;
          } else if (displayMode === 'percentage') {
            const cellText = firstValueCell ? `${totalLabel}: ${percentageText}` : percentageText;
            totalRow.append($('<td class="text-right"></td>').text(cellText));
            firstValueCell = false;
          } else {
            const cellText = firstValueCell ? `${totalLabel}: ${numberText}` : numberText;
            totalRow.append($('<td class="text-right"></td>').text(cellText));
            firstValueCell = false;
          }
        });
        tfoot.append(totalRow);
      }
    }

    renderBody();
    updateSortIndicators();

    searchInput.on('input', function () {
      state.searchQuery = $(this).val() || '';
      renderBody();
      updateSortIndicators();
    });

    sortableHeaders.forEach((header) => {
      header.on('click', function () {
        const sortKey = $(this).data('sortKey');
        if (!sortKey) {
          return;
        }
        if (state.sort && state.sort.type === sortKey.type && state.sort.index === sortKey.index && state.sort.mode === (sortKey.mode || null)) {
          state.sort.direction = state.sort.direction === 'asc' ? 'desc' : 'asc';
        } else {
          state.sort = {
            type: sortKey.type,
            index: sortKey.index,
            mode: sortKey.mode || null,
            direction: 'asc'
          };
        }
        updateSortIndicators();
        renderBody();
      });
    });
  }

  function toNumericValue(value) {
    if (value === null || value === undefined) {
      return null;
    }
    if (typeof value === 'number') {
      return Number.isFinite(value) ? value : null;
    }
    if (typeof value === 'boolean') {
      return value ? 1 : 0;
    }
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (!trimmed) {
        return null;
      }
      const normalizedPercent = trimmed.endsWith('%') ? trimmed.slice(0, -1) : trimmed;
      let working = normalizedPercent.replace(/\s+/g, '').replace(/,/g, '');
      const parenMatch = working.match(/^\((.*)\)$/);
      if (parenMatch && parenMatch[1]) {
        working = `-${parenMatch[1]}`;
      }
      const sanitized = working.replace(/[^0-9+\-Ee.]/g, '');
      if (!sanitized) {
        return null;
      }
      const parsed = Number(sanitized);
      return Number.isFinite(parsed) ? parsed : null;
    }
    if (typeof value === 'object') {
      if (value && value.value !== undefined) {
        return toNumericValue(value.value);
      }
      if (value && value.raw !== undefined) {
        return toNumericValue(value.raw);
      }
    }
    return null;
  }

  function formatNumberValue(value) {
    const numeric = toNumericValue(value);
    if (numeric === null) {
      return '--';
    }
    const normalized = Math.abs(numeric) < 1e-9 ? 0 : numeric;
    if (Number.isInteger(normalized)) {
      return normalized.toLocaleString('en-US');
    }
    return normalized.toLocaleString('en-US', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2
    });
  }

  function formatPercentageValue(value) {
    const numeric = toNumericValue(value);
    if (numeric === null) {
      return '--';
    }
    const normalized = Math.abs(numeric) < 1e-9 ? 0 : numeric;
    const formatted = Number.isInteger(normalized)
      ? normalized.toLocaleString('en-US')
      : normalized.toFixed(2).replace(/\.00$/, '').replace(/0+$/, '').replace(/\.$/, '');
    return `${formatted}%`;
  }

  function normalizeDisplayMode(value, fallback = 'number') {
    if (!value || typeof value !== 'string') {
      return fallback;
    }
    const normalized = value.trim().toLowerCase();
    if (normalized === 'percentage' || normalized === 'percent') {
      return 'percentage';
    }
    if (normalized === 'number_percentage'
      || normalized === 'number and percentage'
      || normalized === 'number-and-percentage'
      || normalized === 'both'
      || normalized === 'numberpercentage') {
      return 'number_percentage';
    }
    if (normalized === 'number') {
      return 'number';
    }
    return fallback;
  }

  function formatHeaderLabel(value) {
    if (value === null || value === undefined) {
      return 'Value';
    }
    const text = String(value).replace(/_/g, ' ').trim();
    if (!text) {
      return 'Value';
    }
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function normalizeWidgetSizeValue(rawValue) {
    if (!rawValue || typeof rawValue !== 'string') {
      return '';
    }
    const trimmed = rawValue.trim();
    if (!trimmed) {
      return '';
    }
    if (trimmed.indexOf(' ') !== -1) {
      return trimmed;
    }
    const lower = trimmed.toLowerCase();
    if (lower.startsWith('col-')) {
      return lower;
    }
    if (/^[a-z]{2,3}-\d{1,2}$/i.test(trimmed)) {
      return `col-${lower}`;
    }
    if (/^\d{1,2}$/.test(trimmed)) {
      return `col-md-${trimmed}`;
    }
    return '';
  }

  function resolveWidgetColumnClass(chartType, widgetOptions) {
    const baseType = (chartType || '').toLowerCase();
    const defaultClass = baseType === 'table' ? 'col-12' : (baseType === 'number' || baseType === 'percentage' ? 'col-md-4' : 'col-md-6');
    const configuredSize = widgetOptions && typeof widgetOptions.widget_size === 'string'
      ? widgetOptions.widget_size
      : widgetOptions && typeof widgetOptions.size === 'string'
        ? widgetOptions.size
        : null;

    if (!configuredSize) {
      return defaultClass;
    }

    const normalizedSize = normalizeWidgetSizeValue(configuredSize);
    if (!normalizedSize) {
      return defaultClass;
    }

    return normalizedSize;
  }

  function setDefaultTimeframe() {
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - DEFAULT_RANGE_DAYS);
    $('#dashboardStart').val(formatDateForInput(start));
    $('#dashboardEnd').val(formatDateForInput(end));
    currentQuickRangeKey = null;
    updateQuickRangeButtons();
    updateRangeSummary(start, end);
  }

  function updateQuickRangeButtons() {
    $('.dashboard-quick-range').removeClass('active');
    if (!currentQuickRangeKey) {
      return;
    }
    $(`.dashboard-quick-range[data-range-key="${currentQuickRangeKey}"]`).addClass('active');
  }

  function updateRangeSummary(start, end) {
    const summaryElement = $('#dashboardViewerRange');
    if (!start || !end) {
      summaryElement.text('--');
      return;
    }
    const startText = start.toLocaleString();
    const endText = end.toLocaleString();
    summaryElement.text(`${startText} → ${endText}`);
  }

  function showFilterError(message) {
    const container = $('#dashboardFilterError');
    container.text(message || 'Invalid timeframe');
    container.removeClass('d-none');
  }

  function clearFilterError() {
    $('#dashboardFilterError').addClass('d-none').text('');
  }

  function getSelectedTimeframe({ allowPartial } = { allowPartial: false }) {
    const startInput = $('#dashboardStart').val();
    const endInput = $('#dashboardEnd').val();

    const startDate = parseInputDate(startInput);
    const endDate = parseInputDate(endInput);

    if (!startDate && !endDate) {
      if (allowPartial) {
        return { start: null, end: null };
      }
      showFilterError('Please choose a valid timeframe.');
      return null;
    }

    if (!endDate && startDate) {
      if (allowPartial) {
        return { start: startDate, end: null };
      }
      showFilterError('End date is required.');
      return null;
    }

    if (!startDate && endDate) {
      if (allowPartial) {
        return { start: null, end: endDate };
      }
      showFilterError('Start date is required.');
      return null;
    }

    if (startDate && endDate && startDate > endDate) {
      showFilterError('Start date must be before end date.');
      return null;
    }

    return { start: startDate, end: endDate };
  }

  function destroyCharts() {
    Object.keys(chartInstances).forEach((key) => {
      try {
        chartInstances[key].destroy();
      } catch (err) {
        console.warn('Failed to destroy chart', key, err);
      }
    });
    chartInstances = {};
  }

  function setViewerLoading(isLoading) {
    $('#dashboardViewerLoading').toggleClass('d-none', !isLoading);
    if (isLoading) {
      $('#dashboardViewerEmptyState').addClass('d-none');
      $('#dashboardWidgets').addClass('d-none');
    }
    $('#dashboardViewerStatus').text(isLoading ? 'Loading...' : 'Idle');
  }

  function setViewerStatus(message, isError) {
    const status = $('#dashboardViewerStatus');
    status.text(message);
    if (isError) {
      status.addClass('text-danger');
    } else {
      status.removeClass('text-danger');
    }
  }

  function updateDashboardActionsState() {
    const hasSelection = Boolean(currentDashboardId);
    $('#editDashboardBtn').prop('disabled', !hasSelection);
    $('#removeDashboardBtn').prop('disabled', !hasSelection);
    $('#dashboardJsonEditBtn').prop('disabled', !hasSelection);
    $('#dashboardJsonExportBtn').prop('disabled', !hasSelection);
  }

  function slugify(text) {
    return String(text || 'dashboard')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      || 'dashboard';
  }

  function getSelectedDashboard() {
    if (!currentDashboardId) {
      return null;
    }
    return dashboardsCache.find((item) => Number(item.id) === Number(currentDashboardId)) || null;
  }

  function migrateWidgetOptions(options) {
    if (!options || typeof options !== 'object') {
      return;
    }
    const candidateValue = Object.prototype.hasOwnProperty.call(options, 'widget_size')
      ? options.widget_size
      : options.size;
    const normalized = normalizeWidgetSizeValue(candidateValue);
    if (normalized) {
      options.widget_size = normalized;
    } else {
      delete options.widget_size;
    }
    if (Object.prototype.hasOwnProperty.call(options, 'size')) {
      delete options.size;
    }
  }

  function normalizeWidgetForExport(widget) {
    if (!widget || typeof widget !== 'object') {
      return;
    }
    if (widget.options && typeof widget.options === 'object') {
      migrateWidgetOptions(widget.options);
    }
    if (widget.definition && typeof widget.definition === 'object' && widget.definition.options && typeof widget.definition.options === 'object') {
      migrateWidgetOptions(widget.definition.options);
    }
    if (Array.isArray(widget.widgets)) {
      widget.widgets.forEach((nestedWidget) => normalizeWidgetForExport(nestedWidget));
    }
  }

  function cloneDashboardForJson(dashboard) {
    if (!dashboard) {
      return null;
    }
    const widgets = Array.isArray(dashboard.widgets) ? JSON.parse(JSON.stringify(dashboard.widgets)) : [];
    widgets.forEach((widget) => normalizeWidgetForExport(widget));
    return {
      id: dashboard.id,
      name: dashboard.name || '',
      description: dashboard.description || '',
      is_shared: !!dashboard.is_shared,
      widgets: widgets
    };
  }

  function openDashboardJsonModal(options = {}) {
    const modal = $('#dashboardJsonModal');
    const dashboard = options.dashboard || null;
    const content = dashboard ? JSON.stringify(cloneDashboardForJson(dashboard), null, 2) : '';
    setDashboardJsonContent(content);
    modal.data('dashboardId', dashboard ? dashboard.id : null);
    modal.data('mode', options.mode || (dashboard ? 'edit' : 'import'));
    $('#dashboardJsonUpdateBtn').prop('disabled', !dashboard);
    $('#dashboardJsonLoadBtn').prop('disabled', !dashboardsCache.length);
    modal.modal('show');
  }

  function downloadDashboardJson(dashboard) {
    if (!dashboard) {
      return;
    }
    const payload = cloneDashboardForJson(dashboard);
    if (!payload) {
      return;
    }
    const filename = `dashboard-${slugify(payload.name)}.json`;
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
      URL.revokeObjectURL(link.href);
      document.body.removeChild(link);
    }, 0);
  }

  function normalizeDashboardPayloadFromJson(rawPayload) {
    const payload = {
      name: typeof rawPayload.name === 'string' ? rawPayload.name : '',
      description: typeof rawPayload.description === 'string' ? rawPayload.description : '',
      widgets: Array.isArray(rawPayload.widgets) ? rawPayload.widgets : []
    };
    payload.is_shared = canShareDashboards ? !!rawPayload.is_shared : false;
    return payload;
  }

  function submitDashboardJson(mode) {
    let parsed;
    try {
      parsed = JSON.parse(getDashboardJsonContent() || '{}');
    } catch (err) {
      notify_error(`Dashboard JSON is invalid: ${err.message}`);
      return;
    }

    if (!parsed || typeof parsed !== 'object') {
      notify_error('Dashboard JSON must be an object.');
      return;
    }

    const payload = normalizeDashboardPayloadFromJson(parsed);

    if (!payload.name) {
      notify_error('Dashboard JSON must include a "name".');
      return;
    }

    if (!Array.isArray(payload.widgets)) {
      notify_error('Dashboard JSON must include a "widgets" array.');
      return;
    }

    const isCreate = mode === 'create';
    const modal = $('#dashboardJsonModal');
    let dashboardId = null;

    if (isCreate) {
      payload.csrf_token = $('#csrf_token').val();
    } else {
      dashboardId = modal.data('dashboardId') || parsed.id || currentDashboardId;
      if (!dashboardId) {
        notify_error('Select a dashboard to update.');
        return;
      }
    }

    const url = isCreate ? buildApiUrl(apiBase) : buildApiUrl(`${apiBase}/${dashboardId}`);
    const method = isCreate ? 'POST' : 'PUT';

    $.ajax({
      url: url,
      method: method,
      contentType: 'application/json',
      data: JSON.stringify(payload)
    }).done((response) => {
      $('#dashboardJsonModal').modal('hide');
      if (response && response.data && response.data.id) {
        currentDashboardId = response.data.id;
      } else if (!isCreate) {
        currentDashboardId = dashboardId;
      }
      loadDashboards(false);
    }).fail((jqXHR) => {
      if (jqXHR.responseJSON && jqXHR.responseJSON.message) {
        notify_error(jqXHR.responseJSON.message);
      } else {
        ajax_notify_error(jqXHR, url);
      }
    });
  }

  function downloadJsonTextareaContent() {
    const content = getDashboardJsonContent();
    if (!content || !content.trim()) {
      notify_error('There is no JSON content to download.');
      return;
    }
    let filename = 'dashboard.json';
    try {
      const parsed = JSON.parse(content);
      if (parsed && parsed.name) {
        filename = `dashboard-${slugify(parsed.name)}.json`;
      }
    } catch (err) {
      // Keep default filename when JSON cannot be parsed.
    }
    const blob = new Blob([content], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
      URL.revokeObjectURL(link.href);
      document.body.removeChild(link);
    }, 0);
  }

  function clearDashboardViewer() {
    destroyCharts();
    currentDashboardId = null;
    updateDashboardState({ lastDashboardId: null });
    $('#dashboardSelector').val('');
    $('#selectedDashboardName').text('Select a dashboard');
    $('#selectedDashboardSharedBadge').addClass('d-none');
    $('#dashboardViewerRange').text('--');
    updateRangeSummary(null, null);
    $('#dashboardWidgets').empty().addClass('d-none');
    $('#dashboardViewerEmptyState').removeClass('d-none').text('Choose a dashboard to display its widgets.');
    setViewerStatus('Idle', false);
    updateDashboardActionsState();
  }

  function normalizeColorList(input) {
    if (!input) {
      return null;
    }
    let entries = [];
    if (Array.isArray(input)) {
      entries = input;
    } else if (typeof input === 'string') {
      entries = input.split(',');
    }
    const trimmed = entries.map((entry) => typeof entry === 'string' ? entry.trim() : null).filter(Boolean);
    return trimmed.length ? trimmed : null;
  }

  function lightenColor(color, alpha) {
    if (typeof color !== 'string' || !color) {
      return color;
    }
    if (color.startsWith('rgba') || color.startsWith('rgb')) {
      return color;
    }
    if (!color.startsWith('#')) {
      return color;
    }
    const hex = color.replace('#', '');
    if (hex.length !== 6) {
      return color;
    }
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    const clampedAlpha = Math.max(0, Math.min(1, alpha));
    return `rgba(${r}, ${g}, ${b}, ${clampedAlpha})`;
  }

  function assignDatasetColors(datasets, chartType, widgetOptions = {}) {
    const explicitColors = normalizeColorList(widgetOptions.colors);
    const primaryColor = typeof widgetOptions.color === 'string' ? widgetOptions.color.trim() : null;

    datasets.forEach((dataset, index) => {
      const datasetColors = normalizeColorList(dataset.colors) || explicitColors;
      const datasetPrimary = typeof dataset.color === 'string' ? dataset.color.trim() : null;
      const baseColor = datasetPrimary || primaryColor || (datasetColors ? datasetColors[index % datasetColors.length] : CHART_COLORS[index % CHART_COLORS.length]);
      if (chartType === 'line') {
        dataset.borderColor = dataset.borderColor || baseColor;
        if (widgetOptions.fill === true) {
          dataset.backgroundColor = dataset.backgroundColor || lightenColor(baseColor, 0.15);
          dataset.fill = true;
        } else {
          dataset.backgroundColor = dataset.backgroundColor || baseColor;
          dataset.fill = false;
        }
        dataset.tension = 0.2;
      } else if (chartType === 'pie') {
        const valueColors = datasetColors || explicitColors;
        if (valueColors && valueColors.length) {
          dataset.backgroundColor = dataset.data.map((_, dataIndex) => valueColors[dataIndex % valueColors.length]);
        } else if (!Array.isArray(dataset.backgroundColor)) {
          dataset.backgroundColor = dataset.data.map((_, dataIndex) => CHART_COLORS[dataIndex % CHART_COLORS.length]);
        }
      } else {
        const valueColors = datasetColors;
        if (valueColors && valueColors.length) {
          const colorArray = dataset.data.map((_, dataIndex) => valueColors[dataIndex % valueColors.length]);
          dataset.backgroundColor = dataset.backgroundColor || colorArray;
          dataset.borderColor = dataset.borderColor || colorArray;
        } else {
          dataset.backgroundColor = dataset.backgroundColor || baseColor;
          dataset.borderColor = dataset.borderColor || baseColor;
        }
        dataset.borderWidth = dataset.borderWidth || 1;
      }
      dataset.hoverBackgroundColor = dataset.hoverBackgroundColor || dataset.backgroundColor;
      dataset.hoverBorderColor = dataset.hoverBorderColor || dataset.borderColor;
    });
  }

  function renderNumericWidget(widget, container) {
    const value = widget.data && widget.data.value !== undefined ? widget.data.value : null;
    const label = widget.data && widget.data.label ? widget.data.label : '';
    const widgetOptions = (widget.definition && widget.definition.options) || widget.options || {};
    let displayValue = '--';

    if (widget.chart_type === 'percentage') {
      displayValue = (widget.data && widget.data.formatted_value)
        || (widget.data && widget.data.formatted_percentage)
        || formatPercentageValue(toNumericValue(value));
    } else {
      displayValue = (widget.data && widget.data.formatted_value)
        || formatNumberValue(value);
    }

    const valueElement = $('<div class="display-3 font-weight-bold mb-1"></div>').text(displayValue !== null && displayValue !== undefined ? displayValue : '--');
    if (typeof widgetOptions.color === 'string') {
      valueElement.css('color', widgetOptions.color);
    }
    const labelElement = $('<small class="text-muted"></small>').text(label);

    container.append(valueElement);
    container.append(labelElement);
  }

  function renderChartWidget(widget, container) {
    const chartData = widget.data;
    if (!chartData || !Array.isArray(chartData.labels)) {
      container.append('<div class="text-muted">No data available.</div>');
      return;
    }

  const widgetOptions = widget.definition && widget.definition.options ? widget.definition.options : {};
  const resolvedDisplayMode = normalizeDisplayMode(widgetOptions.display_mode || widgetOptions.display || chartData.display_mode);
  const effectiveDisplayMode = widget.chart_type === 'pie' && resolvedDisplayMode === 'number' ? 'number_percentage' : resolvedDisplayMode;
    const canvasWrapper = $('<div class="custom-dashboard-chart"></div>');
    const canvas = $('<canvas></canvas>');
    canvasWrapper.append(canvas);
    container.append(canvasWrapper);

    const datasets = chartData.datasets ? chartData.datasets.map((dataset) => Object.assign({}, dataset)) : [];
    assignDatasetColors(datasets, widget.chart_type, widgetOptions);

    const maxLabelLength = Number(widgetOptions.label_max_length) > 0 ? Number(widgetOptions.label_max_length) : 32;
    const trimLabel = (label) => {
      if (typeof label !== 'string') {
        return label;
      }
      if (label.length <= maxLabelLength) {
        return label;
      }
      return `${label.slice(0, maxLabelLength - 1)}...`;
    };

    datasets.forEach((dataset) => {
      const rawData = Array.isArray(dataset.data) ? dataset.data.slice() : [];
      const numericValues = rawData.map((value) => toNumericValue(value));
      const explicitTotal = dataset.total !== undefined ? toNumericValue(dataset.total) : null;
      let computedTotal = explicitTotal;
      if (computedTotal === null) {
        computedTotal = numericValues.reduce((accumulator, value) => {
          if (value === null) {
            return accumulator;
          }
          if (accumulator === null) {
            return value;
          }
          return accumulator + value;
        }, null);
      }
      dataset._irisRawData = rawData;
      dataset._irisNumericValues = numericValues;
      dataset._irisTotal = computedTotal;
      dataset._irisPercentageValues = numericValues.map((value) => {
        if (value === null || computedTotal === null) {
          return null;
        }
        if (computedTotal === 0) {
          return 0;
        }
        return (value / computedTotal) * 100;
      });
      dataset._irisOriginalLabels = Array.isArray(chartData.labels) ? chartData.labels.slice() : [];
      dataset._irisIsPieChart = widget.chart_type === 'pie';
    });

    let displayLabels = Array.isArray(chartData.labels) ? chartData.labels.slice() : [];

    if (widget.chart_type === 'pie' && datasets.length) {
      const primaryDataset = datasets[0];
      const rawValues = Array.isArray(primaryDataset._irisRawData) ? primaryDataset._irisRawData : (Array.isArray(primaryDataset.data) ? primaryDataset.data : []);
      const percentageValues = Array.isArray(primaryDataset._irisPercentageValues) ? primaryDataset._irisPercentageValues : [];
      displayLabels = displayLabels.map((label, index) => {
        const safeLabel = label === null || label === undefined || label === '' ? `Slice ${index + 1}` : String(label);
        const numberText = formatNumberValue(rawValues[index]);
        const percentageRaw = percentageValues[index];
        if (percentageRaw !== null && percentageRaw !== undefined) {
          const percentageText = formatPercentageValue(percentageRaw);
          return `${safeLabel} (${numberText} | ${percentageText})`;
        }
        return `${safeLabel} (${numberText})`;
      });
    }

    const chartConfig = {
      type: widget.chart_type,
      data: {
        labels: displayLabels,
        datasets: datasets
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        legend: {
          display: datasets.length > 1 || widget.chart_type === 'pie'
        },
        layout: {
          padding: {
            top: 8,
            right: 16,
            bottom: 16,
            left: 8
          }
        },
        tooltips: {
          callbacks: {
            title: (tooltipItems, data) => {
              if (!tooltipItems.length) {
                return '';
              }
              const index = tooltipItems[0].index;
              return data.labels && data.labels[index] ? data.labels[index] : '';
            },
            label: (tooltipItem, data) => {
              if (!data || !Array.isArray(data.datasets)) {
                return '';
              }
              const dataset = data.datasets[tooltipItem.datasetIndex] || {};
              const baseLabels = Array.isArray(dataset._irisOriginalLabels) ? dataset._irisOriginalLabels : (Array.isArray(chartData.labels) ? chartData.labels : []);
              const sliceLabel = baseLabels && baseLabels[tooltipItem.index] ? baseLabels[tooltipItem.index] : '';
              const useSliceLabelPrefix = dataset._irisIsPieChart === true;
              const datasetLabel = dataset.label ? `${dataset.label}: ` : (useSliceLabelPrefix && sliceLabel ? `${sliceLabel}: ` : '');
              const rawValues = Array.isArray(dataset._irisRawData) ? dataset._irisRawData : Array.isArray(dataset.data) ? dataset.data : [];
              const numericValues = Array.isArray(dataset._irisNumericValues) ? dataset._irisNumericValues : rawValues.map((value) => toNumericValue(value));
              const rawValue = rawValues[tooltipItem.index];
              const numericValue = numericValues[tooltipItem.index];
              const numberText = formatNumberValue(rawValue);
              const valueKey = dataset.value_key || dataset.valueKey;
              const percentageValues = Array.isArray(dataset._irisPercentageValues) ? dataset._irisPercentageValues : [];
              const percentageRaw = percentageValues[tooltipItem.index];
              const hasPercentage = percentageRaw !== null && percentageRaw !== undefined;
              const percentageText = hasPercentage ? formatPercentageValue(percentageRaw) : null;

              if (effectiveDisplayMode === 'percentage') {
                return `${datasetLabel}${hasPercentage ? percentageText : '--'}`;
              }

              if (effectiveDisplayMode === 'number_percentage') {
                if (hasPercentage && percentageText !== null) {
                  return `${datasetLabel}${numberText} (${percentageText})`;
                }
                return `${datasetLabel}${numberText}`;
              }
              return `${datasetLabel}${numberText}`;
            }
          }
        },
        scales: widget.chart_type === 'pie' ? {} : {
          xAxes: [{
            ticks: {
              autoSkip: true,
              maxRotation: 0,
              minRotation: 0,
              callback: (value) => trimLabel(value)
            }
          }],
          yAxes: [{
            ticks: {
              beginAtZero: true
            }
          }]
        }
      }
    };

    if (widgetOptions.legend_position) {
      chartConfig.options.legend.position = widgetOptions.legend_position;
    }

    const context = canvas.get(0).getContext('2d');
    chartInstances[widget.widget_id] = new Chart(context, chartConfig);
  }

  function renderWidget(widget) {
    const chartType = (widget.chart_type || '').toLowerCase();
    const widgetOptions = (widget.definition && widget.definition.options) || widget.options || {};
    const columnClass = resolveWidgetColumnClass(chartType, widgetOptions);
    const col = $('<div></div>').addClass(`${columnClass} mb-4`);
    const card = $('<div class="card h-100"></div>');
    const header = $('<div class="card-header py-3"></div>');
    const title = $('<h6 class="mb-0"></h6>').text(widget.name || 'Widget');
    header.append(title);

    const body = $('<div class="card-body"></div>');

    if (widget.error) {
      body.append($('<div class="alert alert-warning mb-0"></div>').text(widget.error));
    } else if (!widget.data) {
      body.append('<div class="text-muted">No data available.</div>');
    } else if (chartType === 'table') {
      renderTableWidget(widget, body, widgetOptions);
    } else if (chartType === 'number' || chartType === 'percentage') {
      renderNumericWidget(widget, body);
    } else {
      renderChartWidget(widget, body);
    }

    card.append(header);
    card.append(body);
    col.append(card);
    return col;
  }

  function renderDashboardDetail(payload) {
    destroyCharts();

    const container = $('#dashboardWidgets');
    container.empty();

    const widgets = payload.widgets || [];
    if (!widgets.length) {
      $('#dashboardViewerEmptyState').removeClass('d-none').text('No widgets configured for this dashboard.');
      container.addClass('d-none');
      return;
    }

    widgets.forEach((widget) => {
      container.append(renderWidget(widget));
    });

    $('#dashboardViewerEmptyState').addClass('d-none');
    container.removeClass('d-none');
  }

  function refreshSelectedDashboard(options = {}) {
    if (!currentDashboardId) {
      return;
    }

    if (options.skipIfLoading && isFetchingDashboardData) {
      return;
    }

    const timeframe = getSelectedTimeframe();
    if (!timeframe) {
      return;
    }

    clearFilterError();
    const isSilent = options.silent === true;
    if (!isSilent) {
      setViewerLoading(true);
    } else {
      setViewerStatus('Refreshing...', false);
    }

    const params = {
      start: timeframe.start ? timeframe.start.toISOString() : undefined,
      end: timeframe.end ? timeframe.end.toISOString() : undefined
    };

    isFetchingDashboardData = true;

    $.getJSON(buildApiUrl(`${apiBase}/${currentDashboardId}/data`, params))
      .done((response) => {
        if (!response || response.status !== 'success') {
          if (!isSilent) {
            setViewerLoading(false);
          }
          const errorMessage = response && response.message ? response.message : 'Unable to load dashboard data.';
          setViewerStatus(errorMessage, true);
          $('#dashboardViewerEmptyState').removeClass('d-none').text(errorMessage);
          $('#dashboardWidgets').addClass('d-none');
          if (response && response.message) {
            notify_error(response.message);
          }
          return;
        }

        const data = response.data || {};
        const timeframe = data.timeframe || {};
        const startDate = timeframe.start ? new Date(timeframe.start) : null;
        const endDate = timeframe.end ? new Date(timeframe.end) : null;
        updateRangeSummary(startDate, endDate);
        renderDashboardDetail(data);
        if (!isSilent) {
          setViewerLoading(false);
        }
        const timestamp = new Date().toLocaleTimeString();
        const reason = options.reason === 'auto-refresh' ? 'Auto-refreshed' : 'Loaded';
        setViewerStatus(`${reason} at ${timestamp}`, false);
      })
      .fail((jqXHR) => {
        if (!isSilent) {
          setViewerLoading(false);
        }
        const timestamp = new Date().toLocaleTimeString();
        setViewerStatus(`Error at ${timestamp}`, true);
        if (jqXHR.responseJSON && jqXHR.responseJSON.message) {
          notify_error(jqXHR.responseJSON.message);
        } else {
          ajax_notify_error(jqXHR, `${apiBase}/${currentDashboardId}/data`);
        }
        $('#dashboardViewerEmptyState').removeClass('d-none').text('Failed to load data for the selected dashboard.');
      })
      .always(() => {
        isFetchingDashboardData = false;
      });
  }

  function clearAutoRefreshTimer() {
    if (dashboardAutoRefreshTimer) {
      clearInterval(dashboardAutoRefreshTimer);
      dashboardAutoRefreshTimer = null;
    }
  }

  function setAutoRefresh(enabled, options = {}) {
    const checkbox = $('#dashboardAutoRefresh');
    if (checkbox.length) {
      checkbox.prop('checked', !!enabled);
    }

    clearAutoRefreshTimer();

    if (enabled) {
      const intervalCandidate = options.intervalMs !== undefined ? Number(options.intervalMs) : DASHBOARD_AUTO_REFRESH_INTERVAL_MS;
      const intervalMs = Number.isFinite(intervalCandidate) && intervalCandidate > 0 ? intervalCandidate : DASHBOARD_AUTO_REFRESH_INTERVAL_MS;
      dashboardAutoRefreshTimer = setInterval(() => {
        refreshSelectedDashboard({ skipIfLoading: true, silent: true, reason: 'auto-refresh' });
      }, intervalMs);
    }

    updateDashboardState({ autoRefresh: !!enabled });

    if (enabled && options.skipImmediate !== true) {
      refreshSelectedDashboard({ skipIfLoading: true, silent: options.silent === true, reason: 'auto-refresh' });
    }
  }

  function selectDashboard(dashboardId) {
    const numericId = Number(dashboardId);
    const dashboard = dashboardsCache.find((item) => Number(item.id) === numericId);
    if (!dashboard) {
      clearDashboardViewer();
      return;
    }

    currentDashboardId = dashboard.id;
    updateDashboardState({ lastDashboardId: Number(dashboard.id) });
    $('#dashboardSelector').val(String(dashboard.id));
    $('#selectedDashboardName').text(dashboard.name || 'Dashboard');
    if (dashboard.is_shared) {
      $('#selectedDashboardSharedBadge').removeClass('d-none');
    } else {
      $('#selectedDashboardSharedBadge').addClass('d-none');
    }
    setViewerStatus('Idle', false);
    $('#dashboardViewerEmptyState').removeClass('d-none').text('Loading dashboard data...');
    $('#dashboardWidgets').empty();

    const range = getSelectedTimeframe({ allowPartial: true });
    updateRangeSummary(range ? range.start : null, range ? range.end : null);
    updateDashboardActionsState();

    refreshSelectedDashboard();
  }

  function renderDashboardSelector(dashboards) {
    const selector = $('#dashboardSelector');
    selector.empty();
    selector.append('<option value="">Select a dashboard</option>');

    dashboards.forEach((dashboard) => {
      const optionText = dashboard.is_shared ? `${dashboard.name} (Shared)` : dashboard.name;
      const option = $('<option></option>').val(dashboard.id).text(optionText);
      if (dashboard.description) {
        option.attr('title', dashboard.description);
      }
      selector.append(option);
    });

    if (!dashboards.length) {
      $('#dashboardSelectorEmptyState').removeClass('d-none');
      selector.prop('disabled', true);
    } else {
      $('#dashboardSelectorEmptyState').addClass('d-none');
      selector.prop('disabled', false);
      const exists = dashboards.some((dashboard) => Number(dashboard.id) === Number(currentDashboardId));
      if (exists) {
        selector.val(String(currentDashboardId));
      } else {
        selector.val('');
      }
    }

    updateDashboardActionsState();
  }

  function loadDashboards(autoSelect = true) {
    $.getJSON(buildApiUrl(apiBase))
      .done((response) => {
        if (response.status !== 'success') {
          notify_error(response.message || 'Unable to load dashboards.');
          return;
        }
        dashboardsCache = response.data || [];
        renderDashboardSelector(dashboardsCache);

        if (!dashboardsCache.length) {
          clearDashboardViewer();
          $('#dashboardViewerEmptyState').text('Create your first dashboard to get started.');
          return;
        }

        if (currentDashboardId) {
          const exists = dashboardsCache.some((item) => Number(item.id) === Number(currentDashboardId));
          if (exists) {
            selectDashboard(currentDashboardId);
            return;
          }
        }

        const storedDashboardId = dashboardState && dashboardState.lastDashboardId !== undefined && dashboardState.lastDashboardId !== null
          ? Number(dashboardState.lastDashboardId)
          : null;
        if (storedDashboardId) {
          const exists = dashboardsCache.some((item) => Number(item.id) === storedDashboardId);
          if (exists) {
            selectDashboard(storedDashboardId);
            return;
          }
        }

        if (autoSelect) {
          selectDashboard(dashboardsCache[0].id);
        } else {
          clearDashboardViewer();
        }
      })
      .fail((jqXHR) => {
        ajax_notify_error(jqXHR, apiBase);
      });
  }

  function resetEditor() {
    $('#dashboardEditorForm')[0].reset();
    $('#dashboardId').val('');
    $('#widgetList').empty();
    $('#dashboardIsShared').prop('disabled', !canShareDashboards);
    if (!canShareDashboards) {
      $('#dashboardIsShared').prop('checked', false);
    }
  }

  function addWidgetRow(widget) {
    const widgetId = `widget-${Date.now()}-${Math.random()}`;
    const row = $(
      `<div class="card mb-2 widget-row" data-widget-id="${widgetId}">
        <div class="card-body">
          <div class="form-row">
            <div class="form-group col-md-5">
              <label>Name</label>
              <input type="text" class="form-control widget-name" value="${widget && widget.name ? widget.name : ''}" required>
            </div>
            <div class="form-group col-md-3">
              <label>Visualization</label>
              <select class="form-control widget-chart" required>
                <option value="line">Line</option>
                <option value="bar">Bar</option>
                <option value="pie">Pie</option>
                <option value="number">Number</option>
                <option value="percentage">Percentage</option>
                <option value="table">Table</option>
              </select>
            </div>
            <div class="form-group col-md-2">
              <label>Column class</label>
              <input type="text" class="form-control widget-size" placeholder="col-md-6">
            </div>
            <div class="form-group col-md-2">
              <label>&nbsp;</label>
              <button type="button" class="btn btn-outline-danger btn-block remove-widget">Remove</button>
            </div>
          </div>
          <div class="form-group">
            <label>Configuration (JSON)</label>
            <textarea class="form-control widget-definition" rows="4" placeholder='{"fields": [...]}'>${widget ? JSON.stringify(widget, null, 2) : ''}</textarea>
          </div>
        </div>
      </div>`
    );

    row.find('.widget-chart').val(widget && widget.chart_type ? widget.chart_type : 'line');
    let widgetOptions = {};
    if (widget && widget.options && typeof widget.options === 'object') {
      widgetOptions = widget.options;
    } else if (widget && widget.definition && widget.definition.options && typeof widget.definition.options === 'object') {
      widgetOptions = widget.definition.options;
    }
    const initialSize = typeof widgetOptions.widget_size === 'string'
      ? widgetOptions.widget_size
      : typeof widgetOptions.size === 'string'
        ? widgetOptions.size
        : '';
    if (initialSize) {
      row.find('.widget-size').val(initialSize);
    }
    row.find('.remove-widget').on('click', function () {
      row.remove();
    });

    $('#widgetList').append(row);
  }

  function openEditor(dashboard) {
    resetEditor();
    if (dashboard) {
      $('#dashboardId').val(dashboard.id);
      $('#dashboardName').val(dashboard.name);
      $('#dashboardDescription').val(dashboard.description || '');
      $('#dashboardIsShared').prop('disabled', !canShareDashboards);
      $('#dashboardIsShared').prop('checked', canShareDashboards ? !!dashboard.is_shared : false);
      $('#dashboardEditorTitle').text('Edit dashboard');
      $('#dashboardDeleteBtn').show();
      (dashboard.widgets || []).forEach((widget) => {
        addWidgetRow(widget);
      });
    } else {
      $('#dashboardEditorTitle').text('New dashboard');
      $('#dashboardDeleteBtn').hide();
      $('#dashboardIsShared').prop('disabled', !canShareDashboards);
    }

    $('#dashboardEditorModal').modal('show');
  }

  function collectDashboardPayload() {
    const widgets = [];
    let hasError = false;

    $('.widget-row').each(function () {
      const row = $(this);
      const definitionText = row.find('.widget-definition').val();
      row.find('.widget-definition').removeClass('is-invalid');

      try {
        const definition = JSON.parse(definitionText || '{}');
        definition.name = row.find('.widget-name').val();
        definition.chart_type = row.find('.widget-chart').val();
        const sizeValueRaw = row.find('.widget-size').val();
        const normalizedSizeValue = normalizeWidgetSizeValue(sizeValueRaw);
        if (normalizedSizeValue) {
          definition.options = definition.options && typeof definition.options === 'object' ? definition.options : {};
          definition.options.widget_size = normalizedSizeValue;
          if (Object.prototype.hasOwnProperty.call(definition.options, 'size')) {
            delete definition.options.size;
          }
        } else if (definition.options && typeof definition.options === 'object') {
          if (Object.prototype.hasOwnProperty.call(definition.options, 'widget_size')) {
            delete definition.options.widget_size;
          }
          if (Object.prototype.hasOwnProperty.call(definition.options, 'size')) {
            delete definition.options.size;
          }
          if (!Object.keys(definition.options).length) {
            delete definition.options;
          }
        }
        widgets.push(definition);
      } catch (e) {
        hasError = true;
        row.find('.widget-definition').addClass('is-invalid');
        notify_error(`Widget configuration is not valid JSON: ${e.message}`);
      }
    });

    if (hasError) {
      return null;
    }

    return {
      name: $('#dashboardName').val(),
      description: $('#dashboardDescription').val(),
      is_shared: canShareDashboards ? $('#dashboardIsShared').is(':checked') : false,
      widgets: widgets
    };
  }

  function saveDashboard() {
    const payload = collectDashboardPayload();
    if (!payload) {
      return;
    }

    const dashboardId = $('#dashboardId').val();
    const method = dashboardId ? 'PUT' : 'POST';
    const url = dashboardId ? buildApiUrl(`${apiBase}/${dashboardId}`) : buildApiUrl(apiBase);

    if (method === 'POST') {
      payload.csrf_token = $('#csrf_token').val();
    }

    if (!canShareDashboards) {
      payload.is_shared = false;
    }

    $.ajax({
      url: url,
      method: method,
      contentType: 'application/json',
      data: JSON.stringify(payload)
    }).done(function (response) {
      $('#dashboardEditorModal').modal('hide');
      if (response && response.data && response.data.id) {
        currentDashboardId = response.data.id;
      }
      loadDashboards();
    }).fail(function (jqXHR) {
      if (jqXHR.responseJSON && jqXHR.responseJSON.errors) {
        notify_error('Validation failed while saving the dashboard.');
      } else if (jqXHR.responseJSON && jqXHR.responseJSON.message) {
        notify_error(jqXHR.responseJSON.message);
      } else {
        ajax_notify_error(jqXHR, url);
      }
    });
  }

  function deleteDashboard(dashboardIdOverride) {
    const dashboardIdValue = dashboardIdOverride || $('#dashboardId').val();
    if (!dashboardIdValue) {
      return;
    }

    if (!confirm('Delete this dashboard? This action cannot be undone.')) {
      return;
    }

    const url = buildApiUrl(`${apiBase}/${dashboardIdValue}`);

    $.ajax({
      url: url,
      method: 'DELETE'
    }).done(function () {
      $('#dashboardEditorModal').modal('hide');
      if (Number(dashboardIdValue) === Number(currentDashboardId)) {
        currentDashboardId = null;
      }
      loadDashboards();
    }).fail(function (jqXHR) {
      if (jqXHR.responseJSON && jqXHR.responseJSON.message) {
        notify_error(jqXHR.responseJSON.message);
      } else {
        ajax_notify_error(jqXHR, url);
      }
    });
  }

  function bindEvents() {
    $('#createDashboardBtn').on('click', function () {
      openEditor();
    });

    $('#dashboardSelector').on('change', function () {
      const selectedId = $(this).val();
      if (selectedId) {
        selectDashboard(Number(selectedId));
      } else {
        clearDashboardViewer();
      }
    });

    $('#dashboardHelpBtn').on('click', function () {
      $('#dashboardHelpModal').modal('show');
    });

    $('#dashboardAutoRefresh').on('change', function () {
      const enabled = $(this).is(':checked');
      setAutoRefresh(enabled);
    });

    $('#addWidgetBtn').on('click', function () {
      addWidgetRow();
    });

    $('#dashboardSaveBtn').on('click', function () {
      saveDashboard();
    });

    $('#dashboardDeleteBtn').on('click', function () {
      deleteDashboard();
    });

    $('#editDashboardBtn').on('click', function () {
      if (!currentDashboardId) {
        return;
      }
      const dashboard = dashboardsCache.find((item) => Number(item.id) === Number(currentDashboardId));
      if (dashboard) {
        openEditor(dashboard);
      }
    });

    $('#removeDashboardBtn').on('click', function () {
      if (!currentDashboardId) {
        return;
      }
      deleteDashboard(currentDashboardId);
    });

    $('#dashboardJsonEditBtn').on('click', function () {
      const dashboard = getSelectedDashboard();
      if (!dashboard) {
        notify_error('Select a dashboard to edit as JSON.');
        return;
      }
      openDashboardJsonModal({ mode: 'edit', dashboard });
    });

    $('#dashboardJsonExportBtn').on('click', function () {
      const dashboard = getSelectedDashboard();
      if (!dashboard) {
        notify_error('Select a dashboard to export.');
        return;
      }
      downloadDashboardJson(dashboard);
    });

    $('#dashboardJsonImportBtn').on('click', function () {
      openDashboardJsonModal({ mode: 'import' });
    });

    $('#dashboardJsonDownloadBtn').on('click', function () {
      downloadJsonTextareaContent();
    });

    $('#dashboardJsonImportFileBtn').on('click', function () {
      $('#dashboardJsonFileInput').val('').trigger('click');
    });

    $('#dashboardJsonFileInput').on('change', function (event) {
      const file = event.target.files && event.target.files[0];
      if (!file) {
        return;
      }
      const reader = new FileReader();
      reader.onload = function (loadEvent) {
        setDashboardJsonContent(loadEvent.target.result || '');
        $('#dashboardJsonModal').data('dashboardId', null);
        $('#dashboardJsonUpdateBtn').prop('disabled', true);
      };
      reader.onerror = function () {
        notify_error('Unable to read the selected file.');
      };
      reader.readAsText(file);
      $('#dashboardJsonModal').modal('show');
    });

    $('#dashboardJsonCreateBtn').on('click', function () {
      submitDashboardJson('create');
    });

    $('#dashboardJsonUpdateBtn').on('click', function () {
      submitDashboardJson('update');
    });

    $('#dashboardJsonClearBtn').on('click', function () {
      setDashboardJsonContent('');
      $('#dashboardJsonModal').data('dashboardId', null);
      $('#dashboardJsonUpdateBtn').prop('disabled', true);
      focusDashboardJsonEditor();
    });

    $('#dashboardJsonModal').on('shown.bs.modal', function () {
      setTimeout(function () {
        focusDashboardJsonEditor();
      }, 0);
    });

    $('#dashboardFilterForm').on('submit', function (event) {
      event.preventDefault();
      clearFilterError();
      const timeframe = getSelectedTimeframe();
      if (!timeframe) {
        return;
      }
      currentQuickRangeKey = null;
      updateQuickRangeButtons();
      updateRangeSummary(timeframe.start, timeframe.end);
      refreshSelectedDashboard();
    });

    $('#dashboardResetBtn').on('click', function () {
      setDefaultTimeframe();
      refreshSelectedDashboard();
    });

    $('.dashboard-quick-range').on('click', function () {
      const button = $(this);
      const now = new Date();
      const start = new Date(now);

      if (button.data('minutes')) {
        start.setMinutes(start.getMinutes() - Number(button.data('minutes')));
      }
      if (button.data('hours')) {
        start.setHours(start.getHours() - Number(button.data('hours')));
      }
      if (button.data('days')) {
        start.setDate(start.getDate() - Number(button.data('days')));
      }
      if (button.data('months')) {
        start.setMonth(start.getMonth() - Number(button.data('months')));
      }
      if (button.data('years')) {
        start.setFullYear(start.getFullYear() - Number(button.data('years')));
      }

      $('#dashboardStart').val(formatDateForInput(start));
      $('#dashboardEnd').val(formatDateForInput(now));

      currentQuickRangeKey = button.data('range-key');
      updateQuickRangeButtons();
      updateRangeSummary(start, now);
      clearFilterError();
      refreshSelectedDashboard();
    });
  }

  $(document).ready(function () {
    const shareInput = $('#dashboardCanShare');
    if (shareInput.length) {
      canShareDashboards = shareInput.val() === '1';
    }
    setDefaultTimeframe();
    bindEvents();
    updateDashboardActionsState();
    const autoRefreshEnabled = dashboardState && dashboardState.autoRefresh === true;
    setAutoRefresh(autoRefreshEnabled, { skipImmediate: true, silent: true });
    loadDashboards();
  });
})(jQuery);
