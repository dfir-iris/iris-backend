(function ($) {
  const apiBase = '/custom-dashboards/api/dashboards';
  const DEFAULT_RANGE_DAYS = 30;
  const DASHBOARD_AUTO_REFRESH_INTERVAL_MS = 60000;
  const DASHBOARD_STATE_STORAGE_KEY = 'iris.customDashboards.state';
  const CHART_COLORS = [
    '#4e73df', '#1cc88a', '#36b9cc', '#f6c23e', '#e74a3b', '#858796', '#fd7e14', '#20c997', '#6f42c1', '#17a2b8'
  ];

  const COLOR_PICKER_FALLBACK = '#4e73df';
  const WIDGET_PRESETS_URL = '/static/assets/js/iris/custom_dashboards/widget_presets.json';
  let widgetPresetsCache = null;
  let widgetPresetsMap = null;
  let widgetPresetsLoadPromise = null;

  const TABLE_DEFINITIONS = {
    alerts: {
      label: 'Alerts',
      columns: {
        alert_id: 'Alert ID',
        alert_uuid: 'Alert UUID',
        alert_title: 'Title',
        alert_source: 'Source',
        alert_source_ref: 'Source Reference',
        alert_description: 'Description',
        alert_creation_time: 'Creation Time',
        alert_source_event_time: 'Source Event Time',
        alert_customer_id: 'Customer ID',
        alert_resolution_status_id: 'Resolution Status ID',
        alert_status_id: 'Status ID',
        alert_severity_id: 'Severity ID',
        alert_owner_id: 'Owner ID',
        alert_classification_id: 'Classification ID',
        alert_tags: 'Tags'
      }
    },
    client: {
      label: 'Client',
      columns: {
        client_id: 'Client ID',
        name: 'Client Name'
      }
    },
    alert_resolution_status: {
      label: 'Resolution Status',
      columns: {
        resolution_status_id: 'Resolution Status ID',
        resolution_status_name: 'Resolution Status Name'
      }
    },
    alert_status: {
      label: 'Alert Status',
      columns: {
        status_id: 'Status ID',
        status_name: 'Status Name'
      }
    },
    severities: {
      label: 'Severities',
      columns: {
        severity_id: 'Severity ID',
        severity_name: 'Severity Name'
      }
    },
    case_classification: {
      label: 'Case Classification',
      columns: {
        id: 'Classification ID',
        name: 'Classification Name',
        name_expanded: 'Classification Name (Expanded)'
      }
    },
    cases: {
      label: 'Cases',
      columns: {
        case_id: 'Case ID',
        name: 'Case Name',
        owner_id: 'Owner ID',
        creator_id: 'Creator ID'
      }
    },
    alert_owner: {
      label: 'Alert Owner',
      columns: {
        id: 'Owner ID',
        username: 'Owner Username',
        name: 'Owner Name'
      }
    },
    case_owner: {
      label: 'Case Owner',
      columns: {
        id: 'Owner ID',
        username: 'Owner Username',
        name: 'Owner Name'
      }
    },
    case_creator: {
      label: 'Case Creator',
      columns: {
        id: 'Creator ID',
        username: 'Creator Username',
        name: 'Creator Name'
      }
    },
    case_tags: {
      label: 'Case Tags',
      columns: {
        case_id: 'Case ID',
        tag_id: 'Tag ID'
      }
    },
    tags: {
      label: 'Tags',
      columns: {
        id: 'Tag ID',
        tag_title: 'Tag Title',
        tag_namespace: 'Tag Namespace',
        tag_creation_date: 'Tag Creation Date'
      }
    }
  };

  const AGGREGATION_OPTIONS = [
    { value: '', label: 'None' },
    { value: 'count', label: 'Count' },
    { value: 'sum', label: 'Sum' },
    { value: 'avg', label: 'Average' },
    { value: 'min', label: 'Minimum' },
    { value: 'max', label: 'Maximum' },
    { value: 'ratio', label: 'Ratio (requires filter)' }
  ];

  const OPERATOR_OPTIONS = [
    { value: 'eq', label: 'Equals' },
    { value: 'neq', label: 'Not equals' },
    { value: 'gt', label: 'Greater than' },
    { value: 'gte', label: 'Greater or equal' },
    { value: 'lt', label: 'Less than' },
    { value: 'lte', label: 'Less or equal' },
    { value: 'in', label: 'In list' },
    { value: 'nin', label: 'Not in list' },
    { value: 'between', label: 'Between' },
    { value: 'contains', label: 'Contains (case-insensitive)' }
  ];

  const THRESHOLD_MODE_OPTIONS = [
    { value: 'above', label: 'Above' },
    { value: 'below', label: 'Below' },
    { value: 'between', label: 'Between' },
    { value: 'equal', label: 'Equal' }
  ];

  const TIME_BUCKET_OPTIONS = [
    { value: '', label: 'Auto' },
    { value: 'minute', label: 'Minute' },
    { value: '5minute', label: '5 minutes' },
    { value: '15minute', label: '15 minutes' },
    { value: 'hour', label: 'Hour' },
    { value: 'day', label: 'Day' },
    { value: 'week', label: 'Week' }
  ];

  const SORT_DIRECTION_OPTIONS = [
    { value: '', label: 'Default' },
    { value: 'asc', label: 'Ascending' },
    { value: 'desc', label: 'Descending' }
  ];

  const DISPLAY_MODE_OPTIONS = [
    { value: '', label: 'Automatic' },
    { value: 'number', label: 'Number' },
    { value: 'percentage', label: 'Percentage' },
    { value: 'number_percentage', label: 'Number & percentage' }
  ];

  const WIDGET_SIZE_PRESETS = [
    { value: '', label: 'Auto' },
    { value: 'col-12', label: 'Full width' },
    { value: 'col-lg-8', label: 'Two-thirds' },
    { value: 'col-lg-6', label: 'Half width' },
    { value: 'col-lg-4', label: 'One third' },
    { value: 'col-md-3', label: 'Quarter width' }
  ];

  const LEGEND_POSITION_OPTIONS = [
    { value: '', label: 'Auto' },
    { value: 'top', label: 'Top' },
    { value: 'bottom', label: 'Bottom' },
    { value: 'left', label: 'Left' },
    { value: 'right', label: 'Right' }
  ];

  let builderState = null;
  let builderMode = 'builder';
  let dashboardEditorJsonEditor = null;

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

  function isValidHexColor(value) {
    if (typeof value !== 'string') {
      return false;
    }
    const trimmed = value.trim();
    return /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(trimmed);
  }

  function normalizeHexColor(value) {
    if (!isValidHexColor(value)) {
      return '';
    }
    const trimmed = value.trim();
    if (trimmed.length === 4) {
      const r = trimmed.charAt(1);
      const g = trimmed.charAt(2);
      const b = trimmed.charAt(3);
      return `#${r}${r}${g}${g}${b}${b}`.toLowerCase();
    }
    return trimmed.toLowerCase();
  }

  function getColorPickerValue(value) {
    const normalized = normalizeHexColor(value);
    if (normalized) {
      return normalized;
    }
    return COLOR_PICKER_FALLBACK;
  }

  function normalizePresetEntry(entry) {
    if (!entry || typeof entry !== 'object') {
      return null;
    }
    const key = typeof entry.key === 'string' ? entry.key.trim() : '';
    if (!key) {
      return null;
    }
    const definition = entry.definition && typeof entry.definition === 'object' ? entry.definition : null;
    if (!definition) {
      return null;
    }
    const label = typeof entry.label === 'string' && entry.label.trim() ? entry.label.trim() : key;
    const description = typeof entry.description === 'string' ? entry.description.trim() : '';
    return {
      key,
      label,
      description,
      definition
    };
  }

  function loadWidgetPresets() {
    if (widgetPresetsCache) {
      return Promise.resolve(widgetPresetsCache);
    }
    if (widgetPresetsLoadPromise) {
      return widgetPresetsLoadPromise;
    }

    widgetPresetsLoadPromise = fetch(WIDGET_PRESETS_URL, { cache: 'no-store' })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Failed to load widget templates (${response.status})`);
        }
        return response.json();
      })
      .then((payload) => {
        const entries = Array.isArray(payload)
          ? payload
          : (Array.isArray(payload.presets) ? payload.presets : []);
        const normalized = entries.map((entry) => normalizePresetEntry(entry)).filter((entry) => entry !== null);
        widgetPresetsCache = normalized;
        widgetPresetsMap = new Map(normalized.map((item) => [item.key, item]));
        widgetPresetsLoadPromise = null;
        return normalized;
      })
      .catch((error) => {
        console.error('Failed to load widget templates', error);
        widgetPresetsLoadPromise = null;
        widgetPresetsCache = null;
        widgetPresetsMap = null;
        throw error;
      });

    return widgetPresetsLoadPromise;
  }

  function populatePresetMenu($menu, sectionId) {
    if (!$menu || !$menu.length) {
      return;
    }
    $menu.empty();
    if (!widgetPresetsCache || !widgetPresetsCache.length) {
      $menu.append('<button type="button" class="dropdown-item disabled text-muted">No templates available</button>');
      return;
    }
    widgetPresetsCache.forEach((preset) => {
      const item = $('<button type="button" class="dropdown-item builder-add-preset-widget"></button>');
      item.text(preset.label || preset.key);
      if (preset.description) {
        item.attr('title', preset.description);
      }
      item.attr('data-section-id', sectionId);
      item.attr('data-preset-key', preset.key);
      $menu.append(item);
    });
  }

  function getWidgetPresetByKey(key) {
    if (!key) {
      return null;
    }
    if (widgetPresetsMap && widgetPresetsMap.has(key)) {
      return widgetPresetsMap.get(key);
    }
    return null;
  }

  function generateBuilderId(prefix) {
    const timestamp = Date.now().toString(36);
    const random = Math.random().toString(36).substring(2, 10);
    return `${prefix}-${timestamp}-${random}`;
  }

  function getTableColumns(tableName) {
    const table = TABLE_DEFINITIONS[tableName];
    if (!table || !table.columns) {
      return [];
    }
    return Object.entries(table.columns).map(([column, label]) => ({ value: column, label }));
  }

  function getColumnLabel(tableName, columnName) {
    if (!tableName || !columnName) {
      return columnName;
    }
    const table = TABLE_DEFINITIONS[tableName];
    if (!table || !table.columns) {
      return columnName;
    }
    return table.columns[columnName] || columnName;
  }

  function buildTableColumnOptions(selectedTable, selectedColumn) {
    const columns = getTableColumns(selectedTable);
    if (!columns.length && selectedColumn) {
      columns.push({ value: selectedColumn, label: selectedColumn });
    }
    return columns.map((column) => {
      const option = document.createElement('option');
      option.value = column.value;
      option.textContent = column.label;
      if (column.value === selectedColumn) {
        option.selected = true;
      }
      return option;
    });
  }

  function formatTableColumn(tableName, columnName) {
    if (!tableName || !columnName) {
      return '';
    }
    return `${tableName}.${columnName}`;
  }

  function parseTableColumn(value) {
    if (!value || typeof value !== 'string') {
      return { table: '', column: '' };
    }
    const trimmed = value.trim();
    const [table, column] = trimmed.split('.', 2);
    if (!column) {
      return { table: trimmed, column: '' };
    }
    return { table, column };
  }

  function createEmptyField(overrides = {}) {
    const defaults = {
      id: generateBuilderId('field'),
      table: 'alerts',
      column: 'alert_id',
      aggregation: 'count',
      alias: 'alert_count',
      filter: null
    };
    const field = Object.assign({}, defaults, overrides);
    if (overrides && overrides.filter) {
      field.filter = cloneFieldFilter(overrides.filter);
    } else if (field.filter) {
      field.filter = cloneFieldFilter(field.filter);
    } else {
      field.filter = null;
    }
    return field;
  }

  function createEmptyGroupBy(overrides = {}) {
    const defaults = {
      id: generateBuilderId('group'),
      table: 'client',
      column: 'name'
    };
    return Object.assign(defaults, overrides);
  }

  function createEmptyFilter(overrides = {}) {
    const defaults = {
      id: generateBuilderId('filter'),
      table: 'alerts',
      column: 'alert_status_id',
      operator: 'eq',
      value: ''
    };
    const filter = Object.assign({}, defaults, overrides);
    if (Array.isArray(filter.value)) {
      filter.value = filter.value.slice();
    } else if (filter.value && typeof filter.value === 'object' && !(filter.value instanceof Date)) {
      try {
        filter.value = JSON.parse(JSON.stringify(filter.value));
      } catch (err) {
        filter.value = Object.assign({}, filter.value);
      }
    }
    return filter;
  }

  function cloneFieldFilter(filter) {
    if (!filter || typeof filter !== 'object') {
      return null;
    }
    const cloned = createEmptyFilter(Object.assign({}, filter));
    delete cloned.id;
    return cloned;
  }

  function createEmptyCustomOption(overrides = {}) {
    const defaults = {
      id: generateBuilderId('option'),
      key: '',
      value: ''
    };
    const option = Object.assign({}, defaults, overrides);
    if (Array.isArray(option.value)) {
      option.value = option.value.slice();
    } else if (option.value && typeof option.value === 'object' && !(option.value instanceof Date)) {
      try {
        option.value = JSON.parse(JSON.stringify(option.value));
      } catch (err) {
        option.value = Object.assign({}, option.value);
      }
    }
    return option;
  }

  function normalizeThresholdEntryForState(entry) {
    if (!entry || typeof entry !== 'object') {
      return null;
    }
    const allowedModes = new Set(THRESHOLD_MODE_OPTIONS.map((item) => item.value));
    const rawMode = typeof entry.mode === 'string' ? entry.mode.toLowerCase() : '';
    const mode = allowedModes.has(rawMode) ? rawMode : 'above';
    const endValueSource = entry.end_value !== undefined ? entry.end_value : entry.endValue;
    return {
      mode,
      value: entry.value !== undefined && entry.value !== null ? entry.value : '',
      endValue: endValueSource !== undefined && endValueSource !== null ? endValueSource : '',
      color: typeof entry.color === 'string' ? entry.color : '',
      label: typeof entry.label === 'string' ? entry.label : ''
    };
  }

  function createEmptyThreshold(overrides = {}) {
    const defaults = {
      id: generateBuilderId('threshold'),
      mode: 'above',
      value: '',
      endValue: '',
      color: '',
      label: ''
    };
    const threshold = Object.assign({}, defaults, overrides);
    const allowedModes = new Set(THRESHOLD_MODE_OPTIONS.map((item) => item.value));
    const rawMode = typeof threshold.mode === 'string' ? threshold.mode.toLowerCase().trim() : '';
    threshold.mode = allowedModes.has(rawMode) ? rawMode : 'above';
    threshold.value = threshold.value !== undefined && threshold.value !== null ? String(threshold.value).trim() : '';
    threshold.endValue = threshold.endValue !== undefined && threshold.endValue !== null ? String(threshold.endValue).trim() : '';
    threshold.color = threshold.color !== undefined && threshold.color !== null ? String(threshold.color).trim() : '';
    threshold.label = threshold.label !== undefined && threshold.label !== null ? String(threshold.label).trim() : '';
    return threshold;
  }

  function serializeThresholdForOptions(threshold) {
    if (!threshold) {
      return null;
    }
    const allowedModes = new Set(THRESHOLD_MODE_OPTIONS.map((item) => item.value));
    const rawMode = typeof threshold.mode === 'string' ? threshold.mode.toLowerCase().trim() : '';
    const mode = allowedModes.has(rawMode) ? rawMode : 'above';
    const primaryValue = Number.parseFloat(threshold.value);
    if (!Number.isFinite(primaryValue)) {
      return null;
    }

    const payload = { mode, value: primaryValue };

    const secondaryRaw = threshold.endValue !== undefined && threshold.endValue !== null ? String(threshold.endValue).trim() : '';
    if (mode === 'between') {
      const secondaryValue = Number.parseFloat(secondaryRaw);
      if (!Number.isFinite(secondaryValue)) {
        return null;
      }
      payload.value = Math.min(primaryValue, secondaryValue);
      payload.end_value = Math.max(primaryValue, secondaryValue);
    } else if (secondaryRaw) {
      const secondaryValue = Number.parseFloat(secondaryRaw);
      if (Number.isFinite(secondaryValue)) {
        payload.end_value = secondaryValue;
      }
    }

    if (threshold.color && String(threshold.color).trim()) {
      payload.color = String(threshold.color).trim();
    }
    if (threshold.label && String(threshold.label).trim()) {
      payload.label = String(threshold.label).trim();
    }

    return payload;
  }

  function normalizeThresholdEntryForRuntime(entry) {
    if (!entry || typeof entry !== 'object') {
      return null;
    }
    const allowedModes = new Set(THRESHOLD_MODE_OPTIONS.map((item) => item.value));
    const rawMode = typeof entry.mode === 'string' ? entry.mode.toLowerCase() : '';
    const mode = allowedModes.has(rawMode) ? rawMode : 'above';
    const numericValue = toNumericValue(entry.value);
    if (numericValue === null) {
      return null;
    }
    const endSource = entry.end_value !== undefined ? entry.end_value : entry.endValue;
    const numericEndValue = endSource !== undefined ? toNumericValue(endSource) : null;
    if (mode === 'between' && numericEndValue === null) {
      return null;
    }
    const color = typeof entry.color === 'string' && entry.color.trim() ? entry.color.trim() : '';
    const label = typeof entry.label === 'string' && entry.label.trim() ? entry.label.trim() : '';
    return {
      mode,
      value: numericValue,
      endValue: numericEndValue,
      color,
      label
    };
  }

  function normalizeThresholdArrayFromOptions(options) {
    if (!options || !Array.isArray(options.thresholds)) {
      return [];
    }
    return options.thresholds
      .map((entry) => normalizeThresholdEntryForRuntime(entry))
      .filter((entry) => entry !== null);
  }

  function resolveNumericThresholdMatch(thresholds, numericValue) {
    if (!Array.isArray(thresholds) || thresholds.length === 0) {
      return null;
    }
    if (numericValue === null || numericValue === undefined) {
      return null;
    }

    const EPSILON = 1e-9;

    for (let index = 0; index < thresholds.length; index += 1) {
      const threshold = thresholds[index];
      if (!threshold || threshold.value === null || threshold.value === undefined) {
        continue;
      }
      if (threshold.mode === 'between') {
        if (threshold.endValue === null || threshold.endValue === undefined) {
          continue;
        }
        const min = Math.min(threshold.value, threshold.endValue);
        const max = Math.max(threshold.value, threshold.endValue);
        if (numericValue >= min && numericValue <= max) {
          return threshold;
        }
      } else if (threshold.mode === 'above' && numericValue > threshold.value) {
        return threshold;
      } else if (threshold.mode === 'below' && numericValue < threshold.value) {
        return threshold;
      } else if (threshold.mode === 'equal' && Math.abs(numericValue - threshold.value) <= EPSILON) {
        return threshold;
      }
    }

    return null;
  }

  function createEmptyWidget(overrides = {}) {
    const defaults = {
      id: generateBuilderId('widget'),
      name: 'New widget',
      chartType: 'number',
      widgetSize: '',
      timeBucket: '',
      timeColumn: '',
      fields: [createEmptyField()],
      groupBy: [],
      filters: [],
      thresholds: [],
      options: {
        displayMode: '',
        sortDirection: '',
        limit: '',
        color: '',
        colors: [],
        totalLabel: '',
        legendPosition: '',
        fill: false,
        labelMaxLength: '',
        includeAdvanced: []
      },
      customOptions: [],
      layoutMeta: {},
      collapsed: false
    };

    const widget = Object.assign({}, defaults, overrides);
    if (!Array.isArray(widget.fields) || !widget.fields.length) {
      widget.fields = [createEmptyField()];
    } else {
      widget.fields = widget.fields.map((field) => createEmptyField(field));
    }
    widget.groupBy = Array.isArray(widget.groupBy) ? widget.groupBy.map((group) => createEmptyGroupBy(group)) : [];
    widget.filters = Array.isArray(widget.filters) ? widget.filters.map((filter) => createEmptyFilter(filter)) : [];
    widget.thresholds = Array.isArray(widget.thresholds) ? widget.thresholds.map((threshold) => createEmptyThreshold(threshold)) : [];
    widget.customOptions = Array.isArray(widget.customOptions) ? widget.customOptions.map((option) => createEmptyCustomOption(option)) : [];
    widget.layoutMeta = Object.assign({}, overrides.layoutMeta || defaults.layoutMeta);
    return widget;
  }

  function clonePresetDefinition(definition) {
    return JSON.parse(JSON.stringify(definition));
  }

  function createEmptySection(overrides = {}) {
    const defaults = {
      id: generateBuilderId('section'),
      title: '',
      description: '',
      showDivider: false,
      widgets: []
    };

    const section = Object.assign({}, defaults, overrides);
    section.widgets = Array.isArray(section.widgets) ? section.widgets.map((widget) => createEmptyWidget(widget)) : [];
    return section;
  }

  function parseColorsOption(value) {
    if (Array.isArray(value)) {
      return value.map((entry) => String(entry)).filter((entry) => entry.trim());
    }
    if (typeof value === 'string' && value.trim()) {
      return value.split(',').map((entry) => entry.trim()).filter((entry) => entry);
    }
    return [];
  }

  function normalizeLimitOption(value) {
    if (value === null || value === undefined || value === '') {
      return '';
    }
    if (Number.isFinite(value)) {
      return String(value);
    }
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
    return '';
  }

  function convertWidgetDefinitionToState(definition) {
    const fields = Array.isArray(definition.fields) && definition.fields.length
      ? definition.fields.map((field) => createEmptyField({
        table: field.table,
        column: field.column,
        aggregation: field.aggregation || '',
        alias: field.alias || '',
        filter: field.filter ? cloneFieldFilter(field.filter) : null
      }))
      : [createEmptyField()];

    const groupBy = Array.isArray(definition.group_by)
      ? definition.group_by.map((entry) => {
        const { table, column } = parseTableColumn(entry);
        return createEmptyGroupBy({ table, column });
      })
      : [];

    const filters = Array.isArray(definition.filters)
      ? definition.filters.map((filter) => createEmptyFilter({
        table: filter.table,
        column: filter.column,
        operator: filter.operator,
        value: coerceFilterValue(filter.value, filter.operator)
      }))
      : [];

    const options = (definition.options && typeof definition.options === 'object') ? definition.options : {};
    const handledOptionKeys = new Set(['widget_size', 'size', 'display_mode', 'display', 'sort', 'limit', 'time_column', 'color', 'colors', 'total_label', 'legend_position', 'fill', 'label_max_length', 'thresholds']);

    const customOptions = Object.entries(options)
      .filter(([key]) => !handledOptionKeys.has(key))
      .map(([key, value]) => createEmptyCustomOption({
        key,
        value: value === undefined || value === null ? '' : (typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value))
      }));

    const layoutMeta = (definition.layout && typeof definition.layout === 'object') ? definition.layout : {};

    const thresholds = Array.isArray(options.thresholds)
      ? options.thresholds
        .map((entry) => normalizeThresholdEntryForState(entry))
        .filter(Boolean)
        .map((entry) => createEmptyThreshold(entry))
      : [];

    return createEmptyWidget({
      id: definition.id || layoutMeta.widget_id || generateBuilderId('widget'),
      name: definition.name || 'Widget',
      chartType: definition.chart_type || 'number',
      widgetSize: options.widget_size || options.size || '',
      timeBucket: definition.time_bucket || '',
      timeColumn: options.time_column || '',
      fields,
      groupBy,
      filters,
      options: {
        displayMode: normalizeDisplayMode(options.display_mode || options.display || ''),
        sortDirection: options.sort || '',
        limit: normalizeLimitOption(options.limit),
        color: options.color || '',
        colors: parseColorsOption(options.colors),
        totalLabel: options.total_label || '',
        legendPosition: options.legend_position || '',
        fill: options.fill === true || options.fill === 'true',
        labelMaxLength: options.label_max_length !== undefined && options.label_max_length !== null ? String(options.label_max_length) : ''
      },
      thresholds,
      customOptions,
      layoutMeta
    });
  }

  function normalizeScalarOption(value) {
    if (value === undefined || value === null) {
      return undefined;
    }
    if (typeof value === 'string') {
      const trimmed = value.trim();
      return trimmed === '' ? undefined : trimmed;
    }
    return value;
  }

  function coerceFilterValue(rawValue, operator) {
    if (rawValue === undefined || rawValue === null) {
      return null;
    }
    if (typeof rawValue === 'number' || typeof rawValue === 'boolean') {
      return rawValue;
    }
    if (Array.isArray(rawValue)) {
      return rawValue.slice();
    }
    if (rawValue instanceof Date) {
      return new Date(rawValue.getTime());
    }
    if (typeof rawValue === 'object') {
      try {
        return JSON.parse(JSON.stringify(rawValue));
      } catch (err) {
        return Object.assign({}, rawValue);
      }
    }
    const value = String(rawValue).trim();
    if (!value) {
      return '';
    }
    if (value === 'true' || value === 'false') {
      return value === 'true';
    }
    const numericValue = Number(value);
    if (!Number.isNaN(numericValue) && String(numericValue) === value) {
      return numericValue;
    }
    if ((value.startsWith('[') && value.endsWith(']')) || (value.startsWith('{') && value.endsWith('}'))) {
      try {
        return JSON.parse(value);
      } catch (err) {
        return value;
      }
    }
    if ((operator === 'in' || operator === 'nin') && value.indexOf(',') !== -1) {
      const parts = value.split(',').map((item) => item.trim()).filter((item) => item !== '');
      if (parts.length > 1) {
        return parts;
      }
    }
    return value;
  }

  function formatValueForInput(rawValue, operator) {
    const normalized = coerceFilterValue(rawValue, operator);
    if (normalized === undefined || normalized === null) {
      return '';
    }
    if (typeof normalized === 'string') {
      return normalized;
    }
    if (normalized instanceof Date) {
      return normalized.toISOString();
    }
    if (Array.isArray(normalized) || (typeof normalized === 'object' && normalized !== null)) {
      try {
        return JSON.stringify(normalized);
      } catch (err) {
        return String(normalized);
      }
    }
    return String(normalized);
  }

  function coerceCustomOptionValue(rawValue) {
    if (rawValue === undefined || rawValue === null) {
      return null;
    }
    const value = String(rawValue).trim();
    if (!value) {
      return '';
    }
    if ((value.startsWith('[') && value.endsWith(']')) || (value.startsWith('{') && value.endsWith('}'))) {
      try {
        return JSON.parse(value);
      } catch (err) {
        return value;
      }
    }
    if (value === 'true' || value === 'false') {
      return value === 'true';
    }
    if (!Number.isNaN(Number(value))) {
      return Number(value);
    }
    return value;
  }

  function buildWidgetDefinitionFromState(section, widget, sectionIndex, widgetIndex) {
    const definition = {
      name: widget.name || `Widget ${widgetIndex + 1}`,
      chart_type: widget.chartType || 'number',
      fields: widget.fields.map((field) => {
        const payload = {
          table: field.table,
          column: field.column
        };
        if (field.aggregation) {
          payload.aggregation = field.aggregation;
        }
        if (field.alias) {
          payload.alias = field.alias;
        }
        if (field.filter && field.filter.table && field.filter.column && field.filter.operator) {
          payload.filter = {
            table: field.filter.table,
            column: field.filter.column,
            operator: field.filter.operator,
            value: coerceFilterValue(field.filter.value, field.filter.operator)
          };
        }
        return payload;
      }),
      filters: widget.filters.map((filter) => ({
        table: filter.table,
        column: filter.column,
        operator: filter.operator,
        value: coerceFilterValue(filter.value, filter.operator)
      })),
      group_by: widget.groupBy.map((group) => formatTableColumn(group.table, group.column)).filter((value) => value && value.indexOf('.') !== -1),
      time_bucket: widget.timeBucket ? widget.timeBucket : undefined,
      options: {}
    };

    const options = {};
    if (widget.widgetSize) {
      options.widget_size = widget.widgetSize;
    }
    if (widget.options.displayMode) {
      options.display_mode = widget.options.displayMode;
    }
    if (widget.options.sortDirection) {
      options.sort = widget.options.sortDirection;
    }
    if (widget.options.limit) {
      const numericLimit = Number(widget.options.limit);
      options.limit = Number.isFinite(numericLimit) ? numericLimit : widget.options.limit;
    }
    if (widget.timeColumn) {
      options.time_column = widget.timeColumn;
    }
    if (widget.options.color) {
      options.color = widget.options.color;
    }
    if (Array.isArray(widget.options.colors) && widget.options.colors.length) {
      options.colors = widget.options.colors;
    }
    if (widget.options.totalLabel) {
      options.total_label = widget.options.totalLabel;
    }
    if (widget.options.legendPosition) {
      options.legend_position = widget.options.legendPosition;
    }
    if (widget.options.fill) {
      options.fill = true;
    }
    if (widget.options.labelMaxLength) {
      const numericLabelMax = Number(widget.options.labelMaxLength);
      options.label_max_length = Number.isFinite(numericLabelMax) ? numericLabelMax : widget.options.labelMaxLength;
    }

    const thresholdPayload = Array.isArray(widget.thresholds)
      ? widget.thresholds
        .map((threshold) => serializeThresholdForOptions(threshold))
        .filter((entry) => entry !== null)
      : [];

    if (thresholdPayload.length) {
      options.thresholds = thresholdPayload;
    }

    widget.customOptions.forEach((option) => {
      if (!option.key) {
        return;
      }
      options[option.key] = coerceCustomOptionValue(option.value);
    });

    definition.options = options;

    const layoutMeta = Object.assign({}, widget.layoutMeta || {}, {
      section_id: section.id,
      section_title: section.title,
      section_description: section.description,
      section_index: sectionIndex,
      widget_index: widgetIndex,
      show_divider: !!section.showDivider
    });

    layoutMeta.widget_id = widget.id;

    if (options.widget_size && !layoutMeta.widget_size) {
      layoutMeta.widget_size = options.widget_size;
    }

    definition.layout = layoutMeta;

    return definition;
  }

  function initializeEmptyBuilderState() {
    builderState = {
      sections: [createEmptySection({
        title: 'Main Section',
        showDivider: false,
        widgets: [createEmptyWidget({ name: 'New widget' })]
      })]
    };
  }

  function ensureBuilderState() {
    if (!builderState || !Array.isArray(builderState.sections)) {
      initializeEmptyBuilderState();
    }
  }

  function loadBuilderStateFromPayload(payload) {
    ensureBuilderState();

    const sectionsPayload = Array.isArray(payload.sections) ? payload.sections : [];
    const widgetsPayload = Array.isArray(payload.widgets) ? payload.widgets : [];

    if (sectionsPayload.length) {
      builderState.sections = sectionsPayload.map((sectionPayload) => {
        const widgets = Array.isArray(sectionPayload.widgets) ? sectionPayload.widgets : [];
        return createEmptySection({
          id: sectionPayload.id || generateBuilderId('section'),
          title: sectionPayload.title || '',
          description: sectionPayload.description || '',
          showDivider: !!sectionPayload.show_divider,
          widgets: widgets.map((widgetPayload) => convertWidgetDefinitionToState(widgetPayload))
        });
      }).filter((section) => section);
    } else if (widgetsPayload.length) {
      const sectionId = generateBuilderId('section');
      builderState.sections = [createEmptySection({
        id: sectionId,
        title: payload.name ? `${payload.name} widgets` : 'Main Section',
        description: payload.description || '',
        showDivider: false,
        widgets: widgetsPayload.map((widgetPayload) => convertWidgetDefinitionToState(widgetPayload))
      })];
    } else {
      initializeEmptyBuilderState();
    }

    const hasPersistedWidgets = sectionsPayload.length > 0 || widgetsPayload.length > 0;
    if (hasPersistedWidgets) {
      builderState.sections.forEach((section) => {
        if (!section || !Array.isArray(section.widgets)) {
          return;
        }
        section.widgets.forEach((widget) => {
          if (widget) {
            widget.collapsed = true;
          }
        });
      });
    }

    if (!builderState.sections.length) {
      initializeEmptyBuilderState();
    }
  }

  function buildSectionsPayloadFromState() {
    ensureBuilderState();
    return builderState.sections.map((section, sectionIndex) => ({
      id: section.id || generateBuilderId('section'),
      title: section.title,
      description: section.description,
      show_divider: !!section.showDivider,
      widgets: section.widgets.map((widget, widgetIndex) => buildWidgetDefinitionFromState(section, widget, sectionIndex, widgetIndex))
    }));
  }

  function buildWidgetsPayloadFromSections(sections) {
    const widgets = [];
    sections.forEach((section) => {
      (section.widgets || []).forEach((widget) => {
        widgets.push(widget);
      });
    });
    return widgets;
  }

  function buildDashboardPayloadFromState() {
    const name = $('#dashboardName').val();
    const description = $('#dashboardDescription').val();
    const isShared = canShareDashboards ? $('#dashboardIsShared').is(':checked') : false;

    const sections = buildSectionsPayloadFromState();
    const widgets = buildWidgetsPayloadFromSections(sections);

    return {
      name,
      description,
      is_shared: isShared,
      sections,
      widgets
    };
  }

  function ensureDashboardEditorJsonEditor() {
    if (dashboardEditorJsonEditor) {
      return dashboardEditorJsonEditor;
    }

    const container = document.getElementById('dashboardEditorJson');
    if (!container || typeof ace === 'undefined') {
      return null;
    }

    dashboardEditorJsonEditor = ace.edit(container, {
      autoScrollEditorIntoView: true,
      highlightActiveLine: true,
      minLines: 14,
      maxLines: Infinity
    });
    dashboardEditorJsonEditor.setTheme('ace/theme/tomorrow');
    try {
      dashboardEditorJsonEditor.session.setMode('ace/mode/json');
      dashboardEditorJsonEditor.session.setUseWorker(false);
    } catch (err) {
      console.warn('Unable to configure Ace JSON mode for dashboard editor', err);
    }
    dashboardEditorJsonEditor.session.setUseWrapMode(true);
    dashboardEditorJsonEditor.setOption('showPrintMargin', false);
    dashboardEditorJsonEditor.setOption('showLineNumbers', true);
    dashboardEditorJsonEditor.setOption('tabSize', 2);
    dashboardEditorJsonEditor.setOption('useSoftTabs', true);
    dashboardEditorJsonEditor.renderer.setShowGutter(true);
    dashboardEditorJsonEditor.renderer.setScrollMargin(8, 8);

    const textarea = $('#dashboardEditorJsonTextarea');
    if (textarea.length) {
      dashboardEditorJsonEditor.session.on('change', function () {
        textarea.val(dashboardEditorJsonEditor.getValue());
      });
      textarea.addClass('d-none');
      const initialValue = textarea.val() || '';
      dashboardEditorJsonEditor.setValue(initialValue, -1);
      dashboardEditorJsonEditor.clearSelection();
    }

    return dashboardEditorJsonEditor;
  }

  function setDashboardEditorJsonContent(content) {
    const normalized = typeof content === 'string' ? content : JSON.stringify(content || {}, null, 2);
    $('#dashboardEditorJsonTextarea').val(normalized);
    const editor = ensureDashboardEditorJsonEditor();
    if (editor) {
      editor.setValue(normalized, -1);
      editor.clearSelection();
    }
  }

  function getDashboardEditorJsonContent() {
    const editor = ensureDashboardEditorJsonEditor();
    if (editor) {
      return editor.getValue();
    }
    return $('#dashboardEditorJsonTextarea').val() || '';
  }

  function syncBuilderJsonFromState() {
    const payload = buildDashboardPayloadFromState();
    setDashboardEditorJsonContent(JSON.stringify(payload, null, 2));
  }

  function applyPayloadToEditorForm(payload) {
    if (!payload || typeof payload !== 'object') {
      return;
    }
    if (typeof payload.name === 'string') {
      $('#dashboardName').val(payload.name);
    }
    if (typeof payload.description === 'string') {
      $('#dashboardDescription').val(payload.description);
    }
    $('#dashboardIsShared').prop('disabled', !canShareDashboards);
    if (canShareDashboards) {
      $('#dashboardIsShared').prop('checked', !!payload.is_shared);
    } else {
      $('#dashboardIsShared').prop('checked', false);
    }
  }

  function parseDashboardPayloadFromJsonEditor() {
    const content = getDashboardEditorJsonContent();
    const trimmed = (content || '').trim();
    if (!trimmed) {
      notify_error('Dashboard JSON cannot be empty.');
      return null;
    }

    let parsed;
    try {
      parsed = JSON.parse(trimmed);
    } catch (err) {
      notify_error(`Dashboard JSON is invalid: ${err.message}`);
      return null;
    }

    if (!parsed || typeof parsed !== 'object') {
      notify_error('Dashboard JSON must be an object.');
      return null;
    }

    if (parsed.sections === undefined) {
      parsed.sections = [];
    }
    if (!Array.isArray(parsed.sections)) {
      notify_error('Dashboard JSON "sections" must be an array when provided.');
      return null;
    }

    if (parsed.widgets === undefined) {
      parsed.widgets = [];
    }
    if (!Array.isArray(parsed.widgets)) {
      notify_error('Dashboard JSON "widgets" must be an array when provided.');
      return null;
    }

    if (!canShareDashboards) {
      parsed.is_shared = false;
    } else {
      parsed.is_shared = parsed.is_shared === true;
    }

    if (typeof parsed.name !== 'string') {
      parsed.name = $('#dashboardName').val() || '';
    }
    if (typeof parsed.description !== 'string') {
      parsed.description = $('#dashboardDescription').val() || '';
    }

    return parsed;
  }

  function updateEditorModeControls() {
    const isBuilderMode = builderMode === 'builder';
    $('#dashboardBuilderPanel').toggleClass('d-none', !isBuilderMode);
    $('#dashboardJsonPanel').toggleClass('d-none', isBuilderMode);

    $('#dashboardModeBuilderBtn')
      .toggleClass('btn-primary', isBuilderMode)
      .toggleClass('btn-outline-secondary', !isBuilderMode);
    $('#dashboardModeJsonBtn')
      .toggleClass('btn-primary', !isBuilderMode)
      .toggleClass('btn-outline-secondary', isBuilderMode);
  }

  function switchEditorMode(mode, options = {}) {
    const normalized = mode === 'json' ? 'json' : 'builder';
    const skipSync = options.skipSync === true;

    if (!options.force && normalized === builderMode) {
      updateEditorModeControls();
      return true;
    }

    if (normalized === 'json') {
      if (!skipSync) {
        syncBuilderJsonFromState();
      }
      const editor = ensureDashboardEditorJsonEditor();
      if (!editor) {
        $('#dashboardEditorJsonTextarea').removeClass('d-none');
      }
    }

    if (normalized === 'builder' && builderMode === 'json' && !skipSync) {
      const parsed = parseDashboardPayloadFromJsonEditor();
      if (!parsed) {
        return false;
      }
      applyPayloadToEditorForm(parsed);
      loadBuilderStateFromPayload(parsed);
      renderBuilder();
      syncBuilderJsonFromState();
    }

    builderMode = normalized;
    updateEditorModeControls();
    return true;
  }

  function renderFieldRow(sectionId, widgetId, field) {
    const row = $('<div class="form-row align-items-end dashboard-builder-field-row"></div>');

    const tableGroup = $('<div class="form-group col-md-3"></div>');
    tableGroup.append('<label class="small text-muted text-uppercase">Table</label>');
    const tableSelect = $('<select class="form-control form-control-sm builder-field-table"></select>');
    tableSelect.attr('data-section-id', sectionId);
    tableSelect.attr('data-widget-id', widgetId);
    tableSelect.attr('data-field-id', field.id);
    Object.entries(TABLE_DEFINITIONS).forEach(([tableName, tableDef]) => {
      const option = document.createElement('option');
      option.value = tableName;
      option.textContent = tableDef.label;
      if (tableName === field.table) {
        option.selected = true;
      }
      tableSelect.append(option);
    });
    tableGroup.append(tableSelect);

    const columnGroup = $('<div class="form-group col-md-3"></div>');
    columnGroup.append('<label class="small text-muted text-uppercase">Column</label>');
    const columnSelect = $('<select class="form-control form-control-sm builder-field-column"></select>');
    columnSelect.attr('data-section-id', sectionId);
    columnSelect.attr('data-widget-id', widgetId);
    columnSelect.attr('data-field-id', field.id);
    const columnOptions = buildTableColumnOptions(field.table, field.column);
    if (!columnOptions.length && field.column) {
      const fallbackOption = document.createElement('option');
      fallbackOption.value = field.column;
      fallbackOption.textContent = field.column;
      fallbackOption.selected = true;
      columnSelect.append(fallbackOption);
    } else {
      columnOptions.forEach((option) => columnSelect.append(option));
    }
    columnGroup.append(columnSelect);

    const aggregationGroup = $('<div class="form-group col-md-3"></div>');
    aggregationGroup.append('<label class="small text-muted text-uppercase">Aggregation</label>');
    const aggregationSelect = $('<select class="form-control form-control-sm builder-field-aggregation"></select>');
    aggregationSelect.attr('data-section-id', sectionId);
    aggregationSelect.attr('data-widget-id', widgetId);
    aggregationSelect.attr('data-field-id', field.id);
    AGGREGATION_OPTIONS.forEach((aggregation) => {
      const option = document.createElement('option');
      option.value = aggregation.value;
      option.textContent = aggregation.label;
      if ((aggregation.value || '') === (field.aggregation || '')) {
        option.selected = true;
      }
      aggregationSelect.append(option);
    });
    aggregationGroup.append(aggregationSelect);

    const aliasGroup = $('<div class="form-group col-md-2"></div>');
    aliasGroup.append('<label class="small text-muted text-uppercase">Alias</label>');
    const aliasInput = $('<input type="text" class="form-control form-control-sm builder-field-alias">');
    aliasInput.attr('data-section-id', sectionId);
    aliasInput.attr('data-widget-id', widgetId);
    aliasInput.attr('data-field-id', field.id);
    aliasInput.val(field.alias || '');
    aliasGroup.append(aliasInput);

    const removeGroup = $('<div class="form-group col-md-1 text-right"></div>');
    const removeButton = $('<button type="button" class="btn btn-link text-danger builder-remove-field" title="Remove field"><i class="fas fa-times"></i></button>');
    removeButton.attr('data-section-id', sectionId);
    removeButton.attr('data-widget-id', widgetId);
    removeButton.attr('data-field-id', field.id);
    removeGroup.append(removeButton);

    row.append(tableGroup, columnGroup, aggregationGroup, aliasGroup, removeGroup);
    return row;
  }

  function renderGroupingRow(sectionId, widgetId, group) {
    const row = $('<div class="form-row align-items-end dashboard-builder-group-row"></div>');

    const tableGroup = $('<div class="form-group col-md-4"></div>');
    tableGroup.append('<label class="small text-muted text-uppercase">Table</label>');
    const tableSelect = $('<select class="form-control form-control-sm builder-group-table"></select>');
    tableSelect.attr('data-section-id', sectionId);
    tableSelect.attr('data-widget-id', widgetId);
    tableSelect.attr('data-group-id', group.id);
    Object.entries(TABLE_DEFINITIONS).forEach(([tableName, tableDef]) => {
      const option = document.createElement('option');
      option.value = tableName;
      option.textContent = tableDef.label;
      if (tableName === group.table) {
        option.selected = true;
      }
      tableSelect.append(option);
    });
    tableGroup.append(tableSelect);

    const columnGroup = $('<div class="form-group col-md-6"></div>');
    columnGroup.append('<label class="small text-muted text-uppercase">Column</label>');
    const columnSelect = $('<select class="form-control form-control-sm builder-group-column"></select>');
    columnSelect.attr('data-section-id', sectionId);
    columnSelect.attr('data-widget-id', widgetId);
    columnSelect.attr('data-group-id', group.id);
    const columnOptions = buildTableColumnOptions(group.table, group.column);
    if (!columnOptions.length && group.column) {
      const fallbackOption = document.createElement('option');
      fallbackOption.value = group.column;
      fallbackOption.textContent = group.column;
      fallbackOption.selected = true;
      columnSelect.append(fallbackOption);
    } else {
      columnOptions.forEach((option) => columnSelect.append(option));
    }
    columnGroup.append(columnSelect);

    const removeGroup = $('<div class="form-group col-md-2 text-right"></div>');
    const removeButton = $('<button type="button" class="btn btn-link text-danger builder-remove-group" title="Remove grouping"><i class="fas fa-times"></i></button>');
    removeButton.attr('data-section-id', sectionId);
    removeButton.attr('data-widget-id', widgetId);
    removeButton.attr('data-group-id', group.id);
    removeGroup.append(removeButton);

    row.append(tableGroup, columnGroup, removeGroup);
    return row;
  }

  function renderFilterRow(sectionId, widgetId, filter) {
    const row = $('<div class="form-row align-items-end dashboard-builder-filter-row"></div>');

    const tableGroup = $('<div class="form-group col-lg-3"></div>');
    tableGroup.append('<label class="small text-muted text-uppercase">Table</label>');
    const tableSelect = $('<select class="form-control form-control-sm builder-filter-table"></select>');
    tableSelect.attr('data-section-id', sectionId);
    tableSelect.attr('data-widget-id', widgetId);
    tableSelect.attr('data-filter-id', filter.id);
    Object.entries(TABLE_DEFINITIONS).forEach(([tableName, tableDef]) => {
      const option = document.createElement('option');
      option.value = tableName;
      option.textContent = tableDef.label;
      if (tableName === filter.table) {
        option.selected = true;
      }
      tableSelect.append(option);
    });
    tableGroup.append(tableSelect);

    const columnGroup = $('<div class="form-group col-lg-3"></div>');
    columnGroup.append('<label class="small text-muted text-uppercase">Column</label>');
    const columnSelect = $('<select class="form-control form-control-sm builder-filter-column"></select>');
    columnSelect.attr('data-section-id', sectionId);
    columnSelect.attr('data-widget-id', widgetId);
    columnSelect.attr('data-filter-id', filter.id);
    const columnOptions = buildTableColumnOptions(filter.table, filter.column);
    if (!columnOptions.length && filter.column) {
      const fallbackOption = document.createElement('option');
      fallbackOption.value = filter.column;
      fallbackOption.textContent = filter.column;
      fallbackOption.selected = true;
      columnSelect.append(fallbackOption);
    } else {
      columnOptions.forEach((option) => columnSelect.append(option));
    }
    columnGroup.append(columnSelect);

    const operatorGroup = $('<div class="form-group col-lg-2"></div>');
    operatorGroup.append('<label class="small text-muted text-uppercase">Operator</label>');
    const operatorSelect = $('<select class="form-control form-control-sm builder-filter-operator"></select>');
    operatorSelect.attr('data-section-id', sectionId);
    operatorSelect.attr('data-widget-id', widgetId);
    operatorSelect.attr('data-filter-id', filter.id);
    OPERATOR_OPTIONS.forEach((operator) => {
      const option = document.createElement('option');
      option.value = operator.value;
      option.textContent = operator.label;
      if (operator.value === filter.operator) {
        option.selected = true;
      }
      operatorSelect.append(option);
    });
    operatorGroup.append(operatorSelect);

    const valueGroup = $('<div class="form-group col-lg-3"></div>');
    valueGroup.append('<label class="small text-muted text-uppercase">Value</label>');
    const valueInput = $('<input type="text" class="form-control form-control-sm builder-filter-value" placeholder="Value or JSON list">');
    valueInput.attr('data-section-id', sectionId);
    valueInput.attr('data-widget-id', widgetId);
    valueInput.attr('data-filter-id', filter.id);
  valueInput.val(formatValueForInput(filter.value, filter.operator));
    valueGroup.append(valueInput);

    const removeGroup = $('<div class="form-group col-lg-1 text-right"></div>');
    const removeButton = $('<button type="button" class="btn btn-link text-danger builder-remove-filter" title="Remove filter"><i class="fas fa-times"></i></button>');
    removeButton.attr('data-section-id', sectionId);
    removeButton.attr('data-widget-id', widgetId);
    removeButton.attr('data-filter-id', filter.id);
    removeGroup.append(removeButton);

    row.append(tableGroup, columnGroup, operatorGroup, valueGroup, removeGroup);
    return row;
  }

  function renderThresholdRow(sectionId, widgetId, threshold) {
    const row = $('<div class="form-row align-items-end dashboard-builder-threshold-row"></div>');

    const modeGroup = $('<div class="form-group col-lg-2"></div>');
    modeGroup.append('<label class="small text-muted text-uppercase">Condition</label>');
    const modeSelect = $('<select class="form-control form-control-sm builder-threshold-mode"></select>');
    modeSelect.attr('data-section-id', sectionId);
    modeSelect.attr('data-widget-id', widgetId);
    modeSelect.attr('data-threshold-id', threshold.id);
    THRESHOLD_MODE_OPTIONS.forEach((mode) => {
      const option = document.createElement('option');
      option.value = mode.value;
      option.textContent = mode.label;
      if (mode.value === threshold.mode) {
        option.selected = true;
      }
      modeSelect.append(option);
    });
    modeGroup.append(modeSelect);

    const valueGroup = $('<div class="form-group col-lg-2"></div>');
    valueGroup.append('<label class="small text-muted text-uppercase">Value</label>');
    const valueInput = $('<input type="text" class="form-control form-control-sm builder-threshold-value" placeholder="0">');
    valueInput.attr('data-section-id', sectionId);
    valueInput.attr('data-widget-id', widgetId);
    valueInput.attr('data-threshold-id', threshold.id);
    valueInput.val(threshold.value || '');
    valueGroup.append(valueInput);

    const endGroup = $('<div class="form-group col-lg-2"></div>');
    endGroup.append('<label class="small text-muted text-uppercase">And value</label>');
    const endValueInput = $('<input type="text" class="form-control form-control-sm builder-threshold-end-value" placeholder="10">');
    endValueInput.attr('data-section-id', sectionId);
    endValueInput.attr('data-widget-id', widgetId);
    endValueInput.attr('data-threshold-id', threshold.id);
    endValueInput.val(threshold.endValue || '');
    if (threshold.mode !== 'between') {
      endGroup.addClass('d-none');
    }
    endGroup.append(endValueInput);

  const colorGroup = $('<div class="form-group col-lg-2"></div>');
  colorGroup.append('<label class="small text-muted text-uppercase">Color</label>');
  const colorWrapper = $('<div class="d-flex align-items-center builder-color-picker-container"></div>');
  const colorInput = $('<input type="text" class="form-control form-control-sm builder-threshold-color mr-2" placeholder="#ff0000">');
  colorInput.attr('data-section-id', sectionId);
  colorInput.attr('data-widget-id', widgetId);
  colorInput.attr('data-threshold-id', threshold.id);
  colorInput.val(threshold.color || '');
  const colorPicker = $('<input type="color" class="builder-threshold-color-picker" title="Choose color">');
  colorPicker.attr('data-section-id', sectionId);
  colorPicker.attr('data-widget-id', widgetId);
  colorPicker.attr('data-threshold-id', threshold.id);
  colorPicker.val(getColorPickerValue(threshold.color));
  colorPicker.css({ width: '42px', padding: 0 });
  colorWrapper.append(colorInput, colorPicker);
  colorGroup.append(colorWrapper);

    const labelGroup = $('<div class="form-group col-lg-3"></div>');
    labelGroup.append('<label class="small text-muted text-uppercase">Label (optional)</label>');
    const labelInput = $('<input type="text" class="form-control form-control-sm builder-threshold-label" placeholder="Alerting">');
    labelInput.attr('data-section-id', sectionId);
    labelInput.attr('data-widget-id', widgetId);
    labelInput.attr('data-threshold-id', threshold.id);
    labelInput.val(threshold.label || '');
    labelGroup.append(labelInput);

    const removeGroup = $('<div class="form-group col-lg-1 text-right"></div>');
    const removeButton = $('<button type="button" class="btn btn-link text-danger builder-remove-threshold" title="Remove threshold"><i class="fas fa-times"></i></button>');
    removeButton.attr('data-section-id', sectionId);
    removeButton.attr('data-widget-id', widgetId);
    removeButton.attr('data-threshold-id', threshold.id);
    removeGroup.append(removeButton);

    row.append(modeGroup, valueGroup, endGroup, colorGroup, labelGroup, removeGroup);
    return row;
  }

  function renderCustomOptionRow(sectionId, widgetId, option) {
    const row = $('<div class="form-row align-items-end dashboard-builder-custom-option-row"></div>');

    const keyGroup = $('<div class="form-group col-md-5"></div>');
    keyGroup.append('<label class="small text-muted text-uppercase">Key</label>');
    const keyInput = $('<input type="text" class="form-control form-control-sm builder-custom-option-key" placeholder="option_key">');
    keyInput.attr('data-section-id', sectionId);
    keyInput.attr('data-widget-id', widgetId);
    keyInput.attr('data-option-id', option.id);
    keyInput.val(option.key || '');
    keyGroup.append(keyInput);

    const valueGroup = $('<div class="form-group col-md-6"></div>');
    valueGroup.append('<label class="small text-muted text-uppercase">Value</label>');
    const valueInput = $('<input type="text" class="form-control form-control-sm builder-custom-option-value" placeholder="Value (supports JSON)">');
    valueInput.attr('data-section-id', sectionId);
    valueInput.attr('data-widget-id', widgetId);
    valueInput.attr('data-option-id', option.id);
  valueInput.val(formatValueForInput(option.value));
    valueGroup.append(valueInput);

    const removeGroup = $('<div class="form-group col-md-1 text-right"></div>');
    const removeButton = $('<button type="button" class="btn btn-link text-danger builder-remove-custom-option" title="Remove custom option"><i class="fas fa-times"></i></button>');
    removeButton.attr('data-section-id', sectionId);
    removeButton.attr('data-widget-id', widgetId);
    removeButton.attr('data-option-id', option.id);
    removeGroup.append(removeButton);

    row.append(keyGroup, valueGroup, removeGroup);
    return row;
  }

  function renderWidgetCard(section, widget, sectionIndex, widgetIndex) {
    const card = $('<div class="dashboard-builder-widget" draggable="false"></div>');
    card.attr('data-section-id', section.id);
    card.attr('data-widget-id', widget.id);

    if (widget.collapsed) {
      card.addClass('collapsed');
    }

    const header = $('<div class="dashboard-builder-widget-header d-flex justify-content-between align-items-center"></div>');
    const titleContainer = $('<div class="d-flex align-items-center"></div>');
    const chartBadge = $('<span class="badge badge-primary mr-2"></span>').text(widget.chartType ? widget.chartType.toUpperCase() : 'WIDGET');
    const titleText = $('<strong class="builder-widget-title"></strong>').text(widget.name || 'Widget');
    titleContainer.append(chartBadge, titleText);

    const actionsContainer = $('<div class="dashboard-builder-inline-actions"></div>');
    const collapseBtn = $('<button type="button" class="btn btn-link btn-sm builder-toggle-widget" title="Collapse widget"><i class="fas fa-chevron-up"></i></button>');
    collapseBtn.attr('data-section-id', section.id);
    collapseBtn.attr('data-widget-id', widget.id);
    if (widget.collapsed) {
      collapseBtn.find('i').removeClass('fa-chevron-up').addClass('fa-chevron-down');
    }
    const moveUpBtn = $('<button type="button" class="btn btn-link btn-sm builder-move-widget-up" title="Move up"><i class="fas fa-arrow-up"></i></button>');
    moveUpBtn.attr('data-section-id', section.id);
    moveUpBtn.attr('data-widget-id', widget.id);
    const moveDownBtn = $('<button type="button" class="btn btn-link btn-sm builder-move-widget-down" title="Move down"><i class="fas fa-arrow-down"></i></button>');
    moveDownBtn.attr('data-section-id', section.id);
    moveDownBtn.attr('data-widget-id', widget.id);
    const duplicateBtn = $('<button type="button" class="btn btn-link btn-sm builder-duplicate-widget" title="Duplicate widget"><i class="fas fa-clone"></i></button>');
    duplicateBtn.attr('data-section-id', section.id);
    duplicateBtn.attr('data-widget-id', widget.id);
    const removeBtn = $('<button type="button" class="btn btn-link text-danger btn-sm builder-remove-widget" title="Remove widget"><i class="fas fa-trash"></i></button>');
    removeBtn.attr('data-section-id', section.id);
    removeBtn.attr('data-widget-id', widget.id);
    actionsContainer.append(collapseBtn, moveUpBtn, moveDownBtn, duplicateBtn, removeBtn);

    header.append(titleContainer, actionsContainer);

    const body = $('<div class="dashboard-builder-widget-body"></div>');

    const basicRow = $('<div class="form-row"></div>');

    const nameGroup = $('<div class="form-group col-lg-4"></div>');
    nameGroup.append('<label class="small text-muted text-uppercase">Widget name</label>');
    const nameInput = $('<input type="text" class="form-control form-control-sm builder-widget-name" placeholder="Widget name">');
    nameInput.attr('data-section-id', section.id);
    nameInput.attr('data-widget-id', widget.id);
    nameInput.val(widget.name || '');
    nameGroup.append(nameInput);

    const chartGroup = $('<div class="form-group col-lg-3"></div>');
    chartGroup.append('<label class="small text-muted text-uppercase">Visualization</label>');
    const chartSelect = $('<select class="form-control form-control-sm builder-widget-chart"></select>');
    chartSelect.attr('data-section-id', section.id);
    chartSelect.attr('data-widget-id', widget.id);
    ['line', 'bar', 'pie', 'number', 'percentage', 'table'].forEach((type) => {
      const option = document.createElement('option');
      option.value = type;
      option.textContent = type.charAt(0).toUpperCase() + type.slice(1);
      if (type === widget.chartType) {
        option.selected = true;
      }
      chartSelect.append(option);
    });
    chartGroup.append(chartSelect);

    const sizeGroup = $('<div class="form-group col-lg-3"></div>');
    sizeGroup.append('<label class="small text-muted text-uppercase">Widget width</label>');
    const sizeSelect = $('<select class="form-control form-control-sm builder-widget-size"></select>');
    sizeSelect.attr('data-section-id', section.id);
    sizeSelect.attr('data-widget-id', widget.id);
    WIDGET_SIZE_PRESETS.forEach((preset) => {
      const option = document.createElement('option');
      option.value = preset.value;
      option.textContent = preset.label;
      if (preset.value === (widget.widgetSize || '')) {
        option.selected = true;
      }
      sizeSelect.append(option);
    });
    sizeGroup.append(sizeSelect);

    const bucketGroup = $('<div class="form-group col-lg-2"></div>');
    bucketGroup.append('<label class="small text-muted text-uppercase">Time bucket</label>');
    const bucketSelect = $('<select class="form-control form-control-sm builder-widget-time-bucket"></select>');
    bucketSelect.attr('data-section-id', section.id);
    bucketSelect.attr('data-widget-id', widget.id);
    TIME_BUCKET_OPTIONS.forEach((bucket) => {
      const option = document.createElement('option');
      option.value = bucket.value;
      option.textContent = bucket.label;
      if (bucket.value === (widget.timeBucket || '')) {
        option.selected = true;
      }
      bucketSelect.append(option);
    });
    bucketGroup.append(bucketSelect);

    basicRow.append(nameGroup, chartGroup, sizeGroup, bucketGroup);
    body.append(basicRow);

    const timeColumnGroup = $('<div class="form-group"></div>');
    timeColumnGroup.append('<label class="small text-muted text-uppercase">Time column override</label>');
    const timeColumnInput = $('<input type="text" class="form-control form-control-sm builder-widget-time-column" list="dashboardBuilderColumnOptions" placeholder="alerts.alert_creation_time">');
    timeColumnInput.attr('data-section-id', section.id);
    timeColumnInput.attr('data-widget-id', widget.id);
    timeColumnInput.val(widget.timeColumn || '');
    timeColumnGroup.append(timeColumnInput);
    body.append(timeColumnGroup);

    const fieldsBlock = $('<div class="mb-3"></div>');
    fieldsBlock.append('<h6 class="text-uppercase text-muted small mb-2">Fields</h6>');
    const fieldsContainer = $('<div class="dashboard-builder-fields"></div>');
    widget.fields.forEach((field) => {
      fieldsContainer.append(renderFieldRow(section.id, widget.id, field));
    });
    fieldsBlock.append(fieldsContainer);
    const addFieldBtn = $('<button type="button" class="btn btn-outline-primary btn-sm builder-add-field"><i class="fas fa-plus mr-1"></i>Add field</button>');
    addFieldBtn.attr('data-section-id', section.id);
    addFieldBtn.attr('data-widget-id', widget.id);
    fieldsBlock.append(addFieldBtn);
    body.append(fieldsBlock);

    const groupBlock = $('<div class="mb-3"></div>');
    groupBlock.append('<h6 class="text-uppercase text-muted small mb-2">Grouping</h6>');
    const groupContainer = $('<div class="dashboard-builder-groups"></div>');
    widget.groupBy.forEach((group) => {
      groupContainer.append(renderGroupingRow(section.id, widget.id, group));
    });
    if (!widget.groupBy.length) {
      groupContainer.append('<div class="text-muted small">No grouping columns configured.</div>');
    }
    groupBlock.append(groupContainer);
    const addGroupBtn = $('<button type="button" class="btn btn-outline-secondary btn-sm builder-add-group"><i class="fas fa-plus mr-1"></i>Add grouping</button>');
    addGroupBtn.attr('data-section-id', section.id);
    addGroupBtn.attr('data-widget-id', widget.id);
    groupBlock.append(addGroupBtn);
    body.append(groupBlock);

    const filterBlock = $('<div class="mb-3"></div>');
    filterBlock.append('<h6 class="text-uppercase text-muted small mb-2">Filters</h6>');
    const filterContainer = $('<div class="dashboard-builder-filters"></div>');
    widget.filters.forEach((filter) => {
      filterContainer.append(renderFilterRow(section.id, widget.id, filter));
    });
    if (!widget.filters.length) {
      filterContainer.append('<div class="text-muted small">No filters applied.</div>');
    }
    filterBlock.append(filterContainer);
    const addFilterBtn = $('<button type="button" class="btn btn-outline-secondary btn-sm builder-add-filter"><i class="fas fa-plus mr-1"></i>Add filter</button>');
    addFilterBtn.attr('data-section-id', section.id);
    addFilterBtn.attr('data-widget-id', widget.id);
    filterBlock.append(addFilterBtn);
    body.append(filterBlock);

    const optionsBlock = $('<div class="mb-3"></div>');
    optionsBlock.append('<h6 class="text-uppercase text-muted small mb-2">Visualization options</h6>');
    const optionsRowOne = $('<div class="form-row"></div>');

    const displayGroup = $('<div class="form-group col-lg-3"></div>');
    displayGroup.append('<label class="small text-muted text-uppercase">Display mode</label>');
    const displaySelect = $('<select class="form-control form-control-sm builder-option-display"></select>');
    displaySelect.attr('data-section-id', section.id);
    displaySelect.attr('data-widget-id', widget.id);
    DISPLAY_MODE_OPTIONS.forEach((mode) => {
      const option = document.createElement('option');
      option.value = mode.value;
      option.textContent = mode.label;
      if (mode.value === (widget.options.displayMode || '')) {
        option.selected = true;
      }
      displaySelect.append(option);
    });
    displayGroup.append(displaySelect);

    const sortGroup = $('<div class="form-group col-lg-3"></div>');
    sortGroup.append('<label class="small text-muted text-uppercase">Sort</label>');
    const sortSelect = $('<select class="form-control form-control-sm builder-option-sort"></select>');
    sortSelect.attr('data-section-id', section.id);
    sortSelect.attr('data-widget-id', widget.id);
    SORT_DIRECTION_OPTIONS.forEach((direction) => {
      const option = document.createElement('option');
      option.value = direction.value;
      option.textContent = direction.label;
      if (direction.value === (widget.options.sortDirection || '')) {
        option.selected = true;
      }
      sortSelect.append(option);
    });
    sortGroup.append(sortSelect);

    const limitGroup = $('<div class="form-group col-lg-2"></div>');
    limitGroup.append('<label class="small text-muted text-uppercase">Limit</label>');
    const limitInput = $('<input type="text" class="form-control form-control-sm builder-option-limit" placeholder="Rows limit">');
    limitInput.attr('data-section-id', section.id);
    limitInput.attr('data-widget-id', widget.id);
    limitInput.val(widget.options.limit || '');
    limitGroup.append(limitInput);

    const legendGroup = $('<div class="form-group col-lg-2"></div>');
    legendGroup.append('<label class="small text-muted text-uppercase">Legend</label>');
    const legendSelect = $('<select class="form-control form-control-sm builder-option-legend"></select>');
    legendSelect.attr('data-section-id', section.id);
    legendSelect.attr('data-widget-id', widget.id);
    LEGEND_POSITION_OPTIONS.forEach((position) => {
      const option = document.createElement('option');
      option.value = position.value;
      option.textContent = position.label;
      if (position.value === (widget.options.legendPosition || '')) {
        option.selected = true;
      }
      legendSelect.append(option);
    });
    legendGroup.append(legendSelect);

    const fillGroup = $('<div class="form-group col-lg-2"></div>');
    fillGroup.append('<label class="small text-muted text-uppercase">Fill</label>');
    const fillSwitchWrapper = $('<div class="custom-control custom-switch"></div>');
    const fillId = `builderFill-${section.id}-${widget.id}`;
    const fillInput = $('<input type="checkbox" class="custom-control-input builder-option-fill">');
    fillInput.attr('data-section-id', section.id);
    fillInput.attr('data-widget-id', widget.id);
    fillInput.attr('id', fillId);
    fillInput.prop('checked', !!widget.options.fill);
    const fillLabel = $('<label class="custom-control-label"></label>').attr('for', fillId).text('Area');
    fillSwitchWrapper.append(fillInput, fillLabel);
    fillGroup.append(fillSwitchWrapper);

    optionsRowOne.append(displayGroup, sortGroup, limitGroup, legendGroup, fillGroup);

    const optionsRowTwo = $('<div class="form-row"></div>');

  const colorGroup = $('<div class="form-group col-lg-3"></div>');
  colorGroup.append('<label class="small text-muted text-uppercase">Color</label>');
  const colorWrapper = $('<div class="d-flex align-items-center builder-color-picker-container"></div>');
  const colorInput = $('<input type="text" class="form-control form-control-sm builder-option-color mr-2" placeholder="#4e73df">');
  colorInput.attr('data-section-id', section.id);
  colorInput.attr('data-widget-id', widget.id);
  colorInput.val(widget.options.color || '');
  const colorPicker = $('<input type="color" class="builder-option-color-picker" title="Choose color">');
  colorPicker.attr('data-section-id', section.id);
  colorPicker.attr('data-widget-id', widget.id);
  colorPicker.val(getColorPickerValue(widget.options.color));
  colorPicker.css({ width: '42px', padding: 0 });
  colorWrapper.append(colorInput, colorPicker);
  colorGroup.append(colorWrapper);

    const colorsGroup = $('<div class="form-group col-lg-3"></div>');
    colorsGroup.append('<label class="small text-muted text-uppercase">Palette (comma separated)</label>');
    const colorsInput = $('<input type="text" class="form-control form-control-sm builder-option-colors" placeholder="#4e73df,#1cc88a">');
    colorsInput.attr('data-section-id', section.id);
    colorsInput.attr('data-widget-id', widget.id);
    colorsInput.val(Array.isArray(widget.options.colors) && widget.options.colors.length ? widget.options.colors.join(', ') : '');
    colorsGroup.append(colorsInput);

    const totalLabelGroup = $('<div class="form-group col-lg-3"></div>');
    totalLabelGroup.append('<label class="small text-muted text-uppercase">Total label</label>');
    const totalLabelInput = $('<input type="text" class="form-control form-control-sm builder-option-total-label" placeholder="Grand total">');
    totalLabelInput.attr('data-section-id', section.id);
    totalLabelInput.attr('data-widget-id', widget.id);
    totalLabelInput.val(widget.options.totalLabel || '');
    totalLabelGroup.append(totalLabelInput);

    const labelMaxGroup = $('<div class="form-group col-lg-3"></div>');
    labelMaxGroup.append('<label class="small text-muted text-uppercase">Label max length</label>');
    const labelMaxInput = $('<input type="text" class="form-control form-control-sm builder-option-label-max" placeholder="32">');
    labelMaxInput.attr('data-section-id', section.id);
    labelMaxInput.attr('data-widget-id', widget.id);
    labelMaxInput.val(widget.options.labelMaxLength || '');
    labelMaxGroup.append(labelMaxInput);

    optionsRowTwo.append(colorGroup, colorsGroup, totalLabelGroup, labelMaxGroup);

    optionsBlock.append(optionsRowOne, optionsRowTwo);

    const thresholdsWrapper = $('<div class="mt-3"></div>');
    thresholdsWrapper.append('<h6 class="text-uppercase text-muted small mb-2">Thresholds</h6>');
    const thresholdsContainer = $('<div class="dashboard-builder-thresholds"></div>');
    if (widget.thresholds.length) {
      widget.thresholds.forEach((threshold) => {
        thresholdsContainer.append(renderThresholdRow(section.id, widget.id, threshold));
      });
    } else {
      thresholdsContainer.append('<div class="text-muted small">No thresholds configured. Add one to adjust colors based on values.</div>');
    }
    const addThresholdBtn = $('<button type="button" class="btn btn-outline-secondary btn-sm builder-add-threshold mt-2"><i class="fas fa-plus mr-1"></i>Add threshold</button>');
    addThresholdBtn.attr('data-section-id', section.id);
    addThresholdBtn.attr('data-widget-id', widget.id);
    thresholdsWrapper.append(thresholdsContainer, addThresholdBtn);
    optionsBlock.append(thresholdsWrapper);

    const customOptionsContainer = $('<div class="dashboard-builder-custom-options"></div>');
    widget.customOptions.forEach((option) => {
      customOptionsContainer.append(renderCustomOptionRow(section.id, widget.id, option));
    });
    if (!widget.customOptions.length) {
      customOptionsContainer.append('<div class="text-muted small">No custom options. Use this area for advanced settings.</div>');
    }
    const addCustomOptionBtn = $('<button type="button" class="btn btn-outline-secondary btn-sm builder-add-custom-option mt-2"><i class="fas fa-plus mr-1"></i>Add custom option</button>');
    addCustomOptionBtn.attr('data-section-id', section.id);
    addCustomOptionBtn.attr('data-widget-id', widget.id);

    optionsBlock.append('<h6 class="text-uppercase text-muted small mt-3 mb-2">Custom options</h6>');
    optionsBlock.append(customOptionsContainer, addCustomOptionBtn);

    body.append(optionsBlock);

    const footer = $('<div class="dashboard-builder-widget-footer"></div>');
    footer.append('<span class="text-muted small">Changes are saved when you click “Save” in the modal footer.</span>');

    card.append(header, body, footer);
    return card;
  }

  function renderSectionCard(section, index, totalSections) {
    const card = $('<div class="dashboard-builder-section"></div>');
    card.attr('data-section-id', section.id);

    const header = $('<div class="dashboard-builder-section-header d-flex justify-content-between align-items-center"></div>');
    const title = $('<div class="d-flex align-items-center"></div>');
    title.append('<span class="badge badge-info mr-2">Section</span>');
    const titleText = $('<strong class="builder-section-title"></strong>').text(section.title || 'Untitled section');
    title.append(titleText);

    const actions = $('<div class="dashboard-builder-inline-actions"></div>');
    const moveUpBtn = $('<button type="button" class="btn btn-link btn-sm builder-move-section-up" title="Move up"><i class="fas fa-arrow-up"></i></button>');
    moveUpBtn.attr('data-section-id', section.id);
    const moveDownBtn = $('<button type="button" class="btn btn-link btn-sm builder-move-section-down" title="Move down"><i class="fas fa-arrow-down"></i></button>');
    moveDownBtn.attr('data-section-id', section.id);
    const removeBtn = $('<button type="button" class="btn btn-link text-danger btn-sm builder-remove-section" title="Remove section"><i class="fas fa-trash"></i></button>');
    removeBtn.attr('data-section-id', section.id);
    actions.append(moveUpBtn, moveDownBtn, removeBtn);

    header.append(title, actions);

    const body = $('<div class="dashboard-builder-section-body"></div>');

    const detailsRow = $('<div class="form-row"></div>');
    const titleGroup = $('<div class="form-group col-lg-6"></div>');
    titleGroup.append('<label class="small text-muted text-uppercase">Section title</label>');
    const titleInput = $('<input type="text" class="form-control form-control-sm builder-section-title" placeholder="Section title">');
    titleInput.attr('data-section-id', section.id);
    titleInput.val(section.title || '');
    titleGroup.append(titleInput);

    const descriptionGroup = $('<div class="form-group col-lg-6"></div>');
    descriptionGroup.append('<label class="small text-muted text-uppercase">Section description</label>');
    const descriptionInput = $('<input type="text" class="form-control form-control-sm builder-section-description" placeholder="Optional description">');
    descriptionInput.attr('data-section-id', section.id);
    descriptionInput.val(section.description || '');
    descriptionGroup.append(descriptionInput);

    detailsRow.append(titleGroup, descriptionGroup);
    body.append(detailsRow);

    const dividerId = `builder-section-divider-${section.id}`;
    const dividerSwitch = $('<div class="custom-control custom-switch mb-3"></div>');
    const dividerInput = $('<input type="checkbox" class="custom-control-input builder-section-divider">');
    dividerInput.attr('id', dividerId);
    dividerInput.attr('data-section-id', section.id);
    dividerInput.prop('checked', !!section.showDivider);
    const dividerLabel = $('<label class="custom-control-label"></label>').attr('for', dividerId).text('Show horizontal divider after this section');
    dividerSwitch.append(dividerInput, dividerLabel);
    body.append(dividerSwitch);

    const dividerPreview = $('<div class="dashboard-builder-section-divider-preview"></div>');
    if (!section.showDivider) {
      dividerPreview.addClass('d-none');
    }
    body.append(dividerPreview);

    const widgetsContainer = $('<div class="dashboard-builder-widgets"></div>');
    if (section.widgets.length) {
      section.widgets.forEach((widget, widgetIndex) => {
        widgetsContainer.append(renderWidgetCard(section, widget, index, widgetIndex));
      });
    } else {
      widgetsContainer.append('<div class="dashboard-builder-empty-state">This section has no widgets yet. Add one to get started.</div>');
    }
    body.append(widgetsContainer);

    const footer = $('<div class="dashboard-builder-section-footer d-flex flex-wrap align-items-center"></div>');
    const addWidgetBtn = $('<button type="button" class="btn btn-outline-primary btn-sm builder-add-widget mr-2"><i class="fas fa-plus mr-1"></i>Add widget</button>');
    addWidgetBtn.attr('data-section-id', section.id);
    footer.append(addWidgetBtn);

    const presetGroup = $('<div class="btn-group btn-group-sm builder-preset-dropdown"></div>');
    const presetToggle = $('<button type="button" class="btn btn-outline-secondary btn-sm dropdown-toggle builder-preset-toggle" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false"></button>');
    presetToggle.text('Add from template');
    const presetMenu = $('<div class="dropdown-menu dropdown-menu-right builder-preset-menu"></div>');
    presetMenu.attr('data-section-id', section.id);
    if (widgetPresetsCache) {
      populatePresetMenu(presetMenu, section.id);
      presetMenu.data('loaded', true);
      if (!widgetPresetsCache.length) {
        presetToggle.prop('disabled', true);
      }
    } else {
      presetMenu.append('<button type="button" class="dropdown-item disabled text-muted">Templates load on first use</button>');
    }
    presetGroup.append(presetToggle, presetMenu);
    footer.append(presetGroup);

    card.append(header, body, footer);

    if (index === 0) {
      moveUpBtn.prop('disabled', true);
    }
    if (index === totalSections - 1) {
      moveDownBtn.prop('disabled', true);
    }

    return card;
  }

  function renderBuilder() {
    ensureBuilderState();
    const container = $('#dashboardSectionsContainer');
    const hint = $('#dashboardBuilderEmptyHint');
    container.empty();

    if (!builderState.sections.length) {
      hint.removeClass('d-none');
      container.append('<div class="dashboard-builder-empty-state">No sections configured yet. Add a section to begin building your dashboard.</div>');
      return;
    }

    hint.addClass('d-none');
    builderState.sections.forEach((section, index) => {
      container.append(renderSectionCard(section, index, builderState.sections.length));
    });
  }

  function findSection(sectionId) {
    ensureBuilderState();
    const index = builderState.sections.findIndex((section) => section.id === sectionId);
    if (index === -1) {
      return { section: null, index: -1 };
    }
    return { section: builderState.sections[index], index };
  }

  function findWidget(sectionId, widgetId) {
    const { section, index: sectionIndex } = findSection(sectionId);
    if (!section) {
      return { section: null, sectionIndex: -1, widget: null, widgetIndex: -1 };
    }
    const widgetIndex = section.widgets.findIndex((widget) => widget.id === widgetId);
    if (widgetIndex === -1) {
      return { section, sectionIndex, widget: null, widgetIndex: -1 };
    }
    return { section, sectionIndex, widget: section.widgets[widgetIndex], widgetIndex };
  }

  function findField(sectionId, widgetId, fieldId) {
    const { section, sectionIndex, widget, widgetIndex } = findWidget(sectionId, widgetId);
    if (!widget) {
      return { section, sectionIndex, widget, widgetIndex, field: null, fieldIndex: -1 };
    }
    const fieldIndex = widget.fields.findIndex((field) => field.id === fieldId);
    if (fieldIndex === -1) {
      return { section, sectionIndex, widget, widgetIndex, field: null, fieldIndex: -1 };
    }
    return { section, sectionIndex, widget, widgetIndex, field: widget.fields[fieldIndex], fieldIndex };
  }

  function findGroup(sectionId, widgetId, groupId) {
    const { section, sectionIndex, widget, widgetIndex } = findWidget(sectionId, widgetId);
    if (!widget) {
      return { section, sectionIndex, widget, widgetIndex, group: null, groupIndex: -1 };
    }
    const groupIndex = widget.groupBy.findIndex((group) => group.id === groupId);
    if (groupIndex === -1) {
      return { section, sectionIndex, widget, widgetIndex, group: null, groupIndex: -1 };
    }
    return { section, sectionIndex, widget, widgetIndex, group: widget.groupBy[groupIndex], groupIndex };
  }

  function findFilter(sectionId, widgetId, filterId) {
    const { section, sectionIndex, widget, widgetIndex } = findWidget(sectionId, widgetId);
    if (!widget) {
      return { section, sectionIndex, widget, widgetIndex, filter: null, filterIndex: -1 };
    }
    const filterIndex = widget.filters.findIndex((filter) => filter.id === filterId);
    if (filterIndex === -1) {
      return { section, sectionIndex, widget, widgetIndex, filter: null, filterIndex: -1 };
    }
    return { section, sectionIndex, widget, widgetIndex, filter: widget.filters[filterIndex], filterIndex };
  }

  function findThreshold(sectionId, widgetId, thresholdId) {
    const { section, sectionIndex, widget, widgetIndex } = findWidget(sectionId, widgetId);
    if (!widget) {
      return { section, sectionIndex, widget, widgetIndex, threshold: null, thresholdIndex: -1 };
    }
    const thresholdIndex = widget.thresholds.findIndex((threshold) => threshold.id === thresholdId);
    if (thresholdIndex === -1) {
      return { section, sectionIndex, widget, widgetIndex, threshold: null, thresholdIndex: -1 };
    }
    return { section, sectionIndex, widget, widgetIndex, threshold: widget.thresholds[thresholdIndex], thresholdIndex };
  }

  function findCustomOption(sectionId, widgetId, optionId) {
    const { section, sectionIndex, widget, widgetIndex } = findWidget(sectionId, widgetId);
    if (!widget) {
      return { section, sectionIndex, widget, widgetIndex, option: null, optionIndex: -1 };
    }
    const optionIndex = widget.customOptions.findIndex((option) => option.id === optionId);
    if (optionIndex === -1) {
      return { section, sectionIndex, widget, widgetIndex, option: null, optionIndex: -1 };
    }
    return { section, sectionIndex, widget, widgetIndex, option: widget.customOptions[optionIndex], optionIndex };
  }

  function rerenderSection(sectionId) {
    const sectionElement = $(`.dashboard-builder-section[data-section-id="${sectionId}"]`);
    const { section, index } = findSection(sectionId);
    if (!section || index === -1) {
      renderBuilder();
      return;
    }
    const replacement = renderSectionCard(section, index, builderState.sections.length);
    if (sectionElement.length) {
      sectionElement.replaceWith(replacement);
    } else {
      renderBuilder();
    }
  }

  function rerenderWidget(sectionId, widgetId) {
    const widgetElement = $(`.dashboard-builder-widget[data-section-id="${sectionId}"][data-widget-id="${widgetId}"]`);
    const { section, sectionIndex, widget, widgetIndex } = findWidget(sectionId, widgetId);
    if (!section || widgetIndex === -1) {
      renderBuilder();
      return;
    }
    const replacement = renderWidgetCard(section, widget, sectionIndex, widgetIndex);
    if (widgetElement.length) {
      widgetElement.replaceWith(replacement);
    } else {
      rerenderSection(sectionId);
    }
  }

  function cloneWidget(widget) {
    const clonedFields = widget.fields.map((field) => createEmptyField({
      table: field.table,
      column: field.column,
      aggregation: field.aggregation,
      alias: field.alias
    }));

    const clonedGroupBy = widget.groupBy.map((group) => createEmptyGroupBy({
      table: group.table,
      column: group.column
    }));

    const clonedFilters = widget.filters.map((filter) => createEmptyFilter({
      table: filter.table,
      column: filter.column,
      operator: filter.operator,
      value: filter.value
    }));

    const clonedThresholds = widget.thresholds.map((threshold) => createEmptyThreshold({
      mode: threshold.mode,
      value: threshold.value,
      endValue: threshold.endValue,
      color: threshold.color,
      label: threshold.label
    }));

    const clonedCustomOptions = widget.customOptions.map((option) => createEmptyCustomOption({
      key: option.key,
      value: option.value
    }));

    return createEmptyWidget({
      name: widget.name ? `${widget.name} (copy)` : 'Widget copy',
      chartType: widget.chartType,
      widgetSize: widget.widgetSize,
      timeBucket: widget.timeBucket,
      timeColumn: widget.timeColumn,
      fields: clonedFields,
      groupBy: clonedGroupBy,
      filters: clonedFilters,
      thresholds: clonedThresholds,
      options: {
        displayMode: widget.options.displayMode,
        sortDirection: widget.options.sortDirection,
        limit: widget.options.limit,
        color: widget.options.color,
        colors: Array.isArray(widget.options.colors) ? widget.options.colors.slice() : [],
        totalLabel: widget.options.totalLabel,
        legendPosition: widget.options.legendPosition,
        fill: !!widget.options.fill,
        labelMaxLength: widget.options.labelMaxLength
      },
      customOptions: clonedCustomOptions,
      layoutMeta: Object.assign({}, widget.layoutMeta || {})
    });
  }

  function bindBuilderEvents() {
    $(document).on('click', '#dashboardModeBuilderBtn', function () {
      switchEditorMode('builder');
    });

    $(document).on('click', '#dashboardModeJsonBtn', function () {
      switchEditorMode('json');
    });

    $(document).on('click', '#addSectionBtn', function () {
      ensureBuilderState();
      builderState.sections.push(createEmptySection({
        title: `Section ${builderState.sections.length + 1}`,
        showDivider: false,
        widgets: [createEmptyWidget({ name: 'New widget' })]
      }));
      renderBuilder();
    });

    $(document).on('click', '.builder-remove-section', function () {
      const sectionId = $(this).data('section-id');
      if (!sectionId) {
        return;
      }
      if (!confirm('Remove this section and all widgets inside it?')) {
        return;
      }
      const { index } = findSection(sectionId);
      if (index === -1) {
        return;
      }
      builderState.sections.splice(index, 1);
      renderBuilder();
    });

    $(document).on('click', '.builder-move-section-up', function () {
      const sectionId = $(this).data('section-id');
      const { index } = findSection(sectionId);
      if (index <= 0) {
        return;
      }
      const [section] = builderState.sections.splice(index, 1);
      builderState.sections.splice(index - 1, 0, section);
      renderBuilder();
    });

    $(document).on('click', '.builder-move-section-down', function () {
      const sectionId = $(this).data('section-id');
      const { index } = findSection(sectionId);
      if (index === -1 || index >= builderState.sections.length - 1) {
        return;
      }
      const [section] = builderState.sections.splice(index, 1);
      builderState.sections.splice(index + 1, 0, section);
      renderBuilder();
    });

    $(document).on('input', '.builder-section-title', function () {
      const sectionId = $(this).data('section-id');
      const { section } = findSection(sectionId);
      if (!section) {
        return;
      }
      section.title = $(this).val();
      $(this).closest('.dashboard-builder-section').find('strong.builder-section-title').text(section.title || 'Untitled section');
    });

    $(document).on('input', '.builder-section-description', function () {
      const sectionId = $(this).data('section-id');
      const { section } = findSection(sectionId);
      if (!section) {
        return;
      }
      section.description = $(this).val();
    });

    $(document).on('change', '.builder-section-divider', function () {
      const sectionId = $(this).data('section-id');
      const { section } = findSection(sectionId);
      if (!section) {
        return;
      }
      section.showDivider = $(this).is(':checked');
      const preview = $(this).closest('.dashboard-builder-section').find('.dashboard-builder-section-divider-preview');
      if (section.showDivider) {
        preview.removeClass('d-none');
      } else {
        preview.addClass('d-none');
      }
    });

    $(document).on('click', '.builder-add-widget', function () {
      const sectionId = $(this).data('section-id');
      const { section } = findSection(sectionId);
      if (!section) {
        return;
      }
      section.widgets.push(createEmptyWidget({ name: `Widget ${section.widgets.length + 1}` }));
      rerenderSection(sectionId);
    });

    $(document).on('show.bs.dropdown', '.builder-preset-dropdown', function () {
      const menu = $(this).find('.builder-preset-menu');
      if (!menu.length) {
        return;
      }
      if (menu.data('loaded')) {
        return;
      }
      const sectionId = menu.attr('data-section-id');
      if (widgetPresetsCache) {
        populatePresetMenu(menu, sectionId);
        menu.data('loaded', true);
        if (!widgetPresetsCache.length) {
          $(this).find('.builder-preset-toggle').prop('disabled', true);
        }
        return;
      }
      if (menu.data('loading')) {
        return;
      }
  menu.data('loading', true);
  menu.empty().append('<button type="button" class="dropdown-item disabled text-muted">Loading...</button>');
      loadWidgetPresets()
        .then(() => {
          menu.data('loading', false);
          menu.data('loaded', true);
          populatePresetMenu(menu, sectionId);
          if (!widgetPresetsCache.length) {
            $(this).find('.builder-preset-toggle').prop('disabled', true);
          }
        })
        .catch(() => {
          menu.data('loading', false);
          menu.data('loaded', false);
          menu.empty().append('<button type="button" class="dropdown-item disabled text-danger">Failed to load templates</button>');
        });
    });

    $(document).on('click', '.builder-add-preset-widget', function (event) {
      event.preventDefault();
      const sectionId = $(this).data('section-id');
      const presetKey = $(this).data('preset-key');
      if (!sectionId || !presetKey) {
        return;
      }
      const applyPreset = (preset) => {
        if (!preset) {
          return;
        }
        const { section } = findSection(sectionId);
        if (!section) {
          return;
        }
        const definition = clonePresetDefinition(preset.definition);
        const widgetState = convertWidgetDefinitionToState(definition);
        widgetState.id = generateBuilderId('widget');
        widgetState.collapsed = false;
        section.widgets.push(widgetState);
        rerenderSection(sectionId);
      };

      const preset = getWidgetPresetByKey(presetKey);
      if (preset) {
        applyPreset(preset);
        return;
      }

      loadWidgetPresets()
        .then(() => {
          const resolved = getWidgetPresetByKey(presetKey);
          if (!resolved) {
            alert('Template is no longer available. Please reload the page.');
            return;
          }
          applyPreset(resolved);
        })
        .catch(() => {
          alert('Unable to load templates right now. Please try again later.');
        });
    });

    $(document).on('click', '.builder-remove-widget', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { section, widgetIndex } = findWidget(sectionId, widgetId);
      if (!section || widgetIndex === -1) {
        return;
      }
      if (!confirm('Remove this widget?')) {
        return;
      }
      section.widgets.splice(widgetIndex, 1);
      rerenderSection(sectionId);
    });

    $(document).on('click', '.builder-move-widget-up', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { section, widgetIndex } = findWidget(sectionId, widgetId);
      if (!section || widgetIndex <= 0) {
        return;
      }
      const [widget] = section.widgets.splice(widgetIndex, 1);
      section.widgets.splice(widgetIndex - 1, 0, widget);
      rerenderSection(sectionId);
    });

    $(document).on('click', '.builder-move-widget-down', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { section, widgetIndex } = findWidget(sectionId, widgetId);
      if (!section || widgetIndex === -1 || widgetIndex >= section.widgets.length - 1) {
        return;
      }
      const [widget] = section.widgets.splice(widgetIndex, 1);
      section.widgets.splice(widgetIndex + 1, 0, widget);
      rerenderSection(sectionId);
    });

    $(document).on('click', '.builder-duplicate-widget', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { section, widget, widgetIndex } = findWidget(sectionId, widgetId);
      if (!section || !widget) {
        return;
      }
      const cloned = cloneWidget(widget);
      section.widgets.splice(widgetIndex + 1, 0, cloned);
      rerenderSection(sectionId);
    });

    $(document).on('click', '.builder-toggle-widget', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.collapsed = !widget.collapsed;
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('input', '.builder-widget-name', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.name = $(this).val();
      $(this).closest('.dashboard-builder-widget').find('.builder-widget-title').text(widget.name || 'Widget');
    });

    $(document).on('change', '.builder-widget-chart', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.chartType = $(this).val();
      $(this).closest('.dashboard-builder-widget').find('.badge').first().text(widget.chartType ? widget.chartType.toUpperCase() : 'WIDGET');
    });

    $(document).on('change', '.builder-widget-size', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.widgetSize = $(this).val();
    });

    $(document).on('change', '.builder-widget-time-bucket', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.timeBucket = $(this).val();
    });

    $(document).on('input', '.builder-widget-time-column', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.timeColumn = $(this).val();
    });

    $(document).on('click', '.builder-add-field', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { section, widget } = findWidget(sectionId, widgetId);
      if (!section || !widget) {
        return;
      }
      widget.fields.push(createEmptyField());
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('click', '.builder-remove-field', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const fieldId = $(this).data('field-id');
      const { widget, fieldIndex } = findField(sectionId, widgetId, fieldId);
      if (!widget || fieldIndex === -1) {
        return;
      }
      if (widget.fields.length <= 1) {
        notify_error('A widget must include at least one field.');
        return;
      }
      widget.fields.splice(fieldIndex, 1);
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('change', '.builder-field-table', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const fieldId = $(this).data('field-id');
      const { field } = findField(sectionId, widgetId, fieldId);
      if (!field) {
        return;
      }
      field.table = $(this).val();
      const columns = getTableColumns(field.table);
      field.column = columns.length ? columns[0].value : '';
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('change', '.builder-field-column', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const fieldId = $(this).data('field-id');
      const { field } = findField(sectionId, widgetId, fieldId);
      if (!field) {
        return;
      }
      field.column = $(this).val();
    });

    $(document).on('change', '.builder-field-aggregation', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const fieldId = $(this).data('field-id');
      const { field } = findField(sectionId, widgetId, fieldId);
      if (!field) {
        return;
      }
      const value = $(this).val();
      field.aggregation = value || '';
    });

    $(document).on('input', '.builder-field-alias', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const fieldId = $(this).data('field-id');
      const { field } = findField(sectionId, widgetId, fieldId);
      if (!field) {
        return;
      }
      field.alias = $(this).val();
    });

    $(document).on('click', '.builder-add-group', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.groupBy.push(createEmptyGroupBy());
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('click', '.builder-remove-group', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const groupId = $(this).data('group-id');
      const { widget, groupIndex } = findGroup(sectionId, widgetId, groupId);
      if (!widget || groupIndex === -1) {
        return;
      }
      widget.groupBy.splice(groupIndex, 1);
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('change', '.builder-group-table', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const groupId = $(this).data('group-id');
      const { group } = findGroup(sectionId, widgetId, groupId);
      if (!group) {
        return;
      }
      group.table = $(this).val();
      const columns = getTableColumns(group.table);
      group.column = columns.length ? columns[0].value : '';
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('change', '.builder-group-column', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const groupId = $(this).data('group-id');
      const { group } = findGroup(sectionId, widgetId, groupId);
      if (!group) {
        return;
      }
      group.column = $(this).val();
    });

    $(document).on('click', '.builder-add-filter', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.filters.push(createEmptyFilter());
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('click', '.builder-remove-filter', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const filterId = $(this).data('filter-id');
      const { widget, filterIndex } = findFilter(sectionId, widgetId, filterId);
      if (!widget || filterIndex === -1) {
        return;
      }
      widget.filters.splice(filterIndex, 1);
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('change', '.builder-filter-table', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const filterId = $(this).data('filter-id');
      const { filter } = findFilter(sectionId, widgetId, filterId);
      if (!filter) {
        return;
      }
      filter.table = $(this).val();
      const columns = getTableColumns(filter.table);
      filter.column = columns.length ? columns[0].value : '';
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('change', '.builder-filter-column', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const filterId = $(this).data('filter-id');
      const { filter } = findFilter(sectionId, widgetId, filterId);
      if (!filter) {
        return;
      }
      filter.column = $(this).val();
    });

    $(document).on('change', '.builder-filter-operator', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const filterId = $(this).data('filter-id');
      const { filter } = findFilter(sectionId, widgetId, filterId);
      if (!filter) {
        return;
      }
      filter.operator = $(this).val();
      filter.value = coerceFilterValue(filter.value, filter.operator);
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('input', '.builder-filter-value', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const filterId = $(this).data('filter-id');
      const { filter } = findFilter(sectionId, widgetId, filterId);
      if (!filter) {
        return;
      }
      filter.value = coerceFilterValue($(this).val(), filter.operator);
    });

    $(document).on('blur', '.builder-filter-value', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('click', '.builder-add-threshold', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.thresholds.push(createEmptyThreshold());
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('click', '.builder-remove-threshold', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const thresholdId = $(this).data('threshold-id');
      const { widget, thresholdIndex } = findThreshold(sectionId, widgetId, thresholdId);
      if (!widget || thresholdIndex === -1) {
        return;
      }
      widget.thresholds.splice(thresholdIndex, 1);
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('change', '.builder-threshold-mode', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const thresholdId = $(this).data('threshold-id');
      const { threshold } = findThreshold(sectionId, widgetId, thresholdId);
      if (!threshold) {
        return;
      }
      const nextMode = ($(this).val() || '').toString().toLowerCase();
      threshold.mode = THRESHOLD_MODE_OPTIONS.some((option) => option.value === nextMode) ? nextMode : 'above';
      if (threshold.mode !== 'between') {
        threshold.endValue = '';
      }
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('input', '.builder-threshold-value', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const thresholdId = $(this).data('threshold-id');
      const { threshold } = findThreshold(sectionId, widgetId, thresholdId);
      if (!threshold) {
        return;
      }
      threshold.value = $(this).val();
    });

    $(document).on('input', '.builder-threshold-end-value', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const thresholdId = $(this).data('threshold-id');
      const { threshold } = findThreshold(sectionId, widgetId, thresholdId);
      if (!threshold) {
        return;
      }
      threshold.endValue = $(this).val();
    });

    $(document).on('input', '.builder-threshold-color', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const thresholdId = $(this).data('threshold-id');
      const { threshold } = findThreshold(sectionId, widgetId, thresholdId);
      if (!threshold) {
        return;
      }
      const value = $(this).val();
      threshold.color = value;
      const normalized = normalizeHexColor(value);
      if (normalized) {
        $(this).siblings('.builder-threshold-color-picker').val(normalized);
      }
    });

    $(document).on('input', '.builder-threshold-color-picker', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const thresholdId = $(this).data('threshold-id');
      const { threshold } = findThreshold(sectionId, widgetId, thresholdId);
      if (!threshold) {
        return;
      }
      const value = $(this).val();
      threshold.color = value;
      const textInput = $(this).siblings('.builder-threshold-color');
      if (textInput.length) {
        textInput.val(value);
      }
    });

    $(document).on('input', '.builder-threshold-label', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const thresholdId = $(this).data('threshold-id');
      const { threshold } = findThreshold(sectionId, widgetId, thresholdId);
      if (!threshold) {
        return;
      }
      threshold.label = $(this).val();
    });

    $(document).on('change', '.builder-option-display', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.options.displayMode = $(this).val();
    });

    $(document).on('change', '.builder-option-sort', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.options.sortDirection = $(this).val();
    });

    $(document).on('input', '.builder-option-limit', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.options.limit = $(this).val();
    });

    $(document).on('change', '.builder-option-legend', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.options.legendPosition = $(this).val();
    });

    $(document).on('change', '.builder-option-fill', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.options.fill = $(this).is(':checked');
    });

    $(document).on('input', '.builder-option-color', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      const value = $(this).val();
      widget.options.color = value;
      const normalized = normalizeHexColor(value);
      if (normalized) {
        $(this).siblings('.builder-option-color-picker').val(normalized);
      }
    });

    $(document).on('input', '.builder-option-color-picker', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      const value = $(this).val();
      widget.options.color = value;
      const textInput = $(this).siblings('.builder-option-color');
      if (textInput.length) {
        textInput.val(value);
      }
    });

    $(document).on('input', '.builder-option-colors', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      const raw = $(this).val();
      widget.options.colors = raw.split(',').map((entry) => entry.trim()).filter((entry) => entry);
    });

    $(document).on('input', '.builder-option-total-label', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.options.totalLabel = $(this).val();
    });

    $(document).on('input', '.builder-option-label-max', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.options.labelMaxLength = $(this).val();
    });

    $(document).on('click', '.builder-add-custom-option', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const { widget } = findWidget(sectionId, widgetId);
      if (!widget) {
        return;
      }
      widget.customOptions.push(createEmptyCustomOption());
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('click', '.builder-remove-custom-option', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const optionId = $(this).data('option-id');
      const { widget, optionIndex } = findCustomOption(sectionId, widgetId, optionId);
      if (!widget || optionIndex === -1) {
        return;
      }
      widget.customOptions.splice(optionIndex, 1);
      rerenderWidget(sectionId, widgetId);
    });

    $(document).on('input', '.builder-custom-option-key', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const optionId = $(this).data('option-id');
      const { option } = findCustomOption(sectionId, widgetId, optionId);
      if (!option) {
        return;
      }
      option.key = $(this).val();
    });

    $(document).on('input', '.builder-custom-option-value', function () {
      const sectionId = $(this).data('section-id');
      const widgetId = $(this).data('widget-id');
      const optionId = $(this).data('option-id');
      const { option } = findCustomOption(sectionId, widgetId, optionId);
      if (!option) {
        return;
      }
      option.value = $(this).val();
    });
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
    let numericValue = null;
    if (widget && widget.data) {
      if (widget.data.value !== undefined && widget.data.value !== null) {
        numericValue = toNumericValue(widget.data.value);
      }
      if (numericValue === null && widget.data.formatted_value !== undefined) {
        numericValue = toNumericValue(widget.data.formatted_value);
      }
      if (numericValue === null && widget.data.formatted_percentage !== undefined) {
        numericValue = toNumericValue(widget.data.formatted_percentage);
      }
    }
    if (numericValue === null) {
      numericValue = toNumericValue(displayValue);
    }

    const thresholdMatches = normalizeThresholdArrayFromOptions(widgetOptions);
    const matchedThreshold = resolveNumericThresholdMatch(thresholdMatches, numericValue);
    const baseColor = typeof widgetOptions.color === 'string' ? widgetOptions.color.trim() : '';
    const appliedColor = matchedThreshold && matchedThreshold.color ? matchedThreshold.color : baseColor;
    if (appliedColor) {
      valueElement.css('color', appliedColor);
    }
    const labelElement = $('<small class="text-muted"></small>').text(label);

    container.append(valueElement);
    container.append(labelElement);
    if (matchedThreshold && matchedThreshold.label) {
      const thresholdLabel = $('<div class="small mt-1"></div>').text(matchedThreshold.label);
      if (matchedThreshold.color) {
        thresholdLabel.css('color', matchedThreshold.color);
      } else {
        thresholdLabel.addClass('text-muted');
      }
      container.append(thresholdLabel);
    }
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

    const widgets = Array.isArray(payload.widgets) ? payload.widgets : [];
    const sectionsPayload = Array.isArray(payload.sections) ? payload.sections : [];

    const normalizedSections = sectionsPayload
      .map((section, index) => {
        const sectionWidgets = Array.isArray(section.widgets) ? section.widgets : [];
        if (!sectionWidgets.length) {
          return null;
        }
        return {
          id: section.id || `section-${index}`,
          title: section.title || '',
          description: section.description || '',
          showDivider: !!section.show_divider,
          widgets: sectionWidgets
        };
      })
      .filter(Boolean);

    if (!normalizedSections.length && widgets.length) {
      normalizedSections.push({
        id: 'section-default',
        title: '',
        description: '',
        showDivider: false,
        widgets: widgets
      });
    }

    if (!normalizedSections.length) {
      $('#dashboardViewerEmptyState').removeClass('d-none').text('No widgets configured for this dashboard.');
      container.addClass('d-none');
      return;
    }

    normalizedSections.forEach((section, index) => {
      const sectionColumn = $('<div class="col-12 dashboard-view-section"></div>');

      if (section.title || section.description) {
        const header = $('<div class="dashboard-view-section-header"></div>');
        if (section.title) {
          header.append($('<h5 class="dashboard-view-section-title mb-1"></h5>').text(section.title));
        }
        if (section.description) {
          header.append($('<p class="dashboard-view-section-description mb-0"></p>').text(section.description));
        }
        sectionColumn.append(header);
      }

      const widgetsRow = $('<div class="row dashboard-view-section-widgets"></div>');
      section.widgets.forEach((widget) => {
        widgetsRow.append(renderWidget(widget));
      });
      sectionColumn.append(widgetsRow);
      container.append(sectionColumn);

      if (section.showDivider && index < normalizedSections.length - 1) {
        container.append('<div class="col-12"><div class="dashboard-view-section-divider"></div></div>');
      }
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
    const form = $('#dashboardEditorForm')[0];
    if (form) {
      form.reset();
    }
    $('#dashboardId').val('');
    builderMode = 'builder';
    initializeEmptyBuilderState();
    renderBuilder();
    if (dashboardEditorJsonEditor) {
      setDashboardEditorJsonContent('');
    } else {
      $('#dashboardEditorJsonTextarea').val('');
    }
    updateEditorModeControls();
    $('#dashboardIsShared').prop('disabled', !canShareDashboards);
    if (!canShareDashboards) {
      $('#dashboardIsShared').prop('checked', false);
    }
    $('#dashboardDeleteBtn').hide();
  }

  function openEditor(dashboard) {
    resetEditor();

    let payload = {
      name: '',
      description: '',
      is_shared: false,
      sections: [],
      widgets: []
    };

    if (dashboard) {
      $('#dashboardId').val(dashboard.id);
      $('#dashboardEditorTitle').text('Edit dashboard');
      $('#dashboardDeleteBtn').show();

      const candidatePayload = dashboard.definition && typeof dashboard.definition === 'object'
        ? Object.assign({}, dashboard.definition)
        : {
            name: dashboard.name || '',
            description: dashboard.description || '',
            is_shared: dashboard.is_shared,
            sections: Array.isArray(dashboard.sections) ? dashboard.sections : [],
            widgets: Array.isArray(dashboard.widgets) ? dashboard.widgets : []
          };

      payload = Object.assign(payload, candidatePayload);
    } else {
      $('#dashboardEditorTitle').text('New dashboard');
      $('#dashboardDeleteBtn').hide();
    }

    payload.name = typeof payload.name === 'string' ? payload.name : '';
    payload.description = typeof payload.description === 'string' ? payload.description : '';
    payload.sections = Array.isArray(payload.sections) ? payload.sections : [];
    payload.widgets = Array.isArray(payload.widgets) ? payload.widgets : [];
    payload.is_shared = canShareDashboards ? !!payload.is_shared : false;

    applyPayloadToEditorForm(payload);
    loadBuilderStateFromPayload(payload);
    renderBuilder();
    syncBuilderJsonFromState();
    switchEditorMode('builder', { force: true, skipSync: true });

    $('#dashboardEditorModal').modal('show');
  }

  function collectDashboardPayload() {
    if (builderMode === 'json') {
      const parsed = parseDashboardPayloadFromJsonEditor();
      if (!parsed) {
        return null;
      }
      applyPayloadToEditorForm(parsed);
      loadBuilderStateFromPayload(parsed);
      renderBuilder();
      syncBuilderJsonFromState();
    }

    const payload = buildDashboardPayloadFromState();
    if (!payload.widgets.length) {
      notify_error('Add at least one widget before saving the dashboard.');
      return null;
    }
    return payload;
  }

  function flattenValidationErrors(errors, prefix) {
    const messages = [];
    if (Array.isArray(errors)) {
      errors.forEach((entry) => {
        messages.push(...flattenValidationErrors(entry, prefix));
      });
      return messages;
    }
    if (errors && typeof errors === 'object') {
      Object.entries(errors).forEach(([key, value]) => {
        const nextPrefix = prefix ? `${prefix}.${key}` : key;
        messages.push(...flattenValidationErrors(value, nextPrefix));
      });
      return messages;
    }
    if (errors !== undefined && errors !== null) {
      const text = String(errors).trim();
      if (text) {
        messages.push(prefix ? `${prefix}: ${text}` : text);
      }
    }
    return messages;
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
        const details = flattenValidationErrors(jqXHR.responseJSON.errors);
        const message = details.length
          ? `Validation failed while saving the dashboard: ${details.join(' | ')}`
          : 'Validation failed while saving the dashboard.';
        notify_error(message);
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
    bindBuilderEvents();

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
