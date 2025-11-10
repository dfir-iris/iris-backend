(function ($) {
  const apiBase = '/custom-dashboards/api/dashboards';
  const DEFAULT_RANGE_DAYS = 30;
  const CHART_COLORS = ['#177dff', '#5e72e4', '#fdaf4b', '#f3545d', '#2dce89', '#11cdef', '#ff6b6b', '#ffa21d', '#9f7aea', '#45aaf2'];

  let dashboardsCache = [];
  let currentDashboardId = null;
  let chartInstances = {};
  let currentQuickRangeKey = null;
  const canShareDashboards = ($('#dashboardCanShare').val() || '0') === '1';

  function buildApiUrl(path, query) {
    const params = new URLSearchParams();
    const caseId = $('#currentCaseId').val();

    if (query) {
      Object.keys(query).forEach((key) => {
        const value = query[key];
        if (value !== null && value !== undefined && value !== '') {
          params.append(key, value);
        }
      });
    }

    if (caseId) {
      params.append('cid', caseId);
    }

    const qs = params.toString();
    if (!qs) {
      return path;
    }
    return `${path}?${qs}`;
  }

  function formatDateForInput(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
      return '';
    }
    const pad = (value) => String(value).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function parseInputDate(value) {
    if (!value) {
      return null;
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return null;
    }
    return parsed;
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
  }

  function clearDashboardViewer() {
    destroyCharts();
    currentDashboardId = null;
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
    let displayValue = value;

    if (widget.chart_type === 'percentage' && value !== null && value !== undefined && !Number.isNaN(Number(value))) {
      displayValue = `${Number(value).toFixed(2)}%`;
    }

    const valueElement = $('<div class="display-3 font-weight-bold mb-1"></div>').text(displayValue !== null && displayValue !== undefined ? displayValue : '--');
    if (typeof widgetOptions.color === 'string') {
      valueElement.css('color', widgetOptions.color);
    }
    const labelElement = $('<small class="text-muted"></small>').text(label);

    container.append(valueElement);
    container.append(labelElement);

    if (widget.data && Array.isArray(widget.data.rows) && widget.data.rows.length) {
      const details = $('<pre class="mt-3 small text-muted"></pre>').text(JSON.stringify(widget.data.rows, null, 2));
      container.append(details);
    }
  }

  function renderChartWidget(widget, container) {
    const chartData = widget.data;
    if (!chartData || !Array.isArray(chartData.labels)) {
      container.append('<div class="text-muted">No data available.</div>');
      return;
    }

    const widgetOptions = widget.definition && widget.definition.options ? widget.definition.options : {};
    const canvasWrapper = $('<div class="custom-dashboard-chart"></div>');
    const canvas = $('<canvas></canvas>');
    canvasWrapper.append(canvas);
    container.append(canvasWrapper);

    const datasets = chartData.datasets ? chartData.datasets.slice() : [];
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

    const chartConfig = {
      type: widget.chart_type,
      data: {
        labels: chartData.labels,
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
    const columnClass = widget.chart_type === 'number' || widget.chart_type === 'percentage' ? 'col-md-4' : 'col-md-6';
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
    } else if (widget.chart_type === 'number' || widget.chart_type === 'percentage') {
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

  function refreshSelectedDashboard() {
    if (!currentDashboardId) {
      return;
    }

    const timeframe = getSelectedTimeframe();
    if (!timeframe) {
      return;
    }

    clearFilterError();
    setViewerLoading(true);

    const params = {
      start: timeframe.start ? timeframe.start.toISOString() : undefined,
      end: timeframe.end ? timeframe.end.toISOString() : undefined
    };

    $.getJSON(buildApiUrl(`${apiBase}/${currentDashboardId}/data`, params))
      .done((response) => {
        if (!response || response.status !== 'success') {
          setViewerLoading(false);
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
        setViewerLoading(false);
        setViewerStatus('Loaded', false);
      })
      .fail((jqXHR) => {
        setViewerLoading(false);
        setViewerStatus('Error', true);
        if (jqXHR.responseJSON && jqXHR.responseJSON.message) {
          notify_error(jqXHR.responseJSON.message);
        } else {
          ajax_notify_error(jqXHR, `${apiBase}/${currentDashboardId}/data`);
        }
        $('#dashboardViewerEmptyState').removeClass('d-none').text('Failed to load data for the selected dashboard.');
      });
  }

  function selectDashboard(dashboardId) {
    const numericId = Number(dashboardId);
    const dashboard = dashboardsCache.find((item) => Number(item.id) === numericId);
    if (!dashboard) {
      clearDashboardViewer();
      return;
    }

    currentDashboardId = dashboard.id;
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
    const row = $(`
      <div class="card mb-2 widget-row" data-widget-id="${widgetId}">
        <div class="card-body">
          <div class="form-row">
            <div class="form-group col-md-6">
              <label>Name</label>
              <input type="text" class="form-control widget-name" value="${widget && widget.name ? widget.name : ''}" required>
            </div>
            <div class="form-group col-md-4">
              <label>Visualization</label>
              <select class="form-control widget-chart" required>
                <option value="line">Line</option>
                <option value="bar">Bar</option>
                <option value="pie">Pie</option>
                <option value="number">Number</option>
                <option value="percentage">Percentage</option>
              </select>
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
      </div>
    `);

    row.find('.widget-chart').val(widget && widget.chart_type ? widget.chart_type : 'line');
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
    setDefaultTimeframe();
    bindEvents();
    updateDashboardActionsState();
    loadDashboards();
  });
})(jQuery);
