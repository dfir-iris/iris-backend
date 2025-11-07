const STATS_DEFAULT_RANGE_DAYS = 30;
const STATS_CASE_STATUS_OPTIONS = [
  { id: '', label: 'All statuses' },
  { id: 0, label: 'Unknown' },
  { id: 1, label: 'False Positive' },
  { id: 2, label: 'True Positive (Impact)' },
  { id: 3, label: 'Not Applicable' },
  { id: 4, label: 'True Positive (No Impact)' },
  { id: 5, label: 'Legitimate' }
];
const STATS_CHART_COLORS = ['#177dff', '#5e72e4', '#fdaf4b', '#f3545d', '#2dce89', '#11cdef', '#ff6b6b', '#ffa21d', '#9f7aea', '#45aaf2'];
const QUEUE_CARD_CLASSES = ['critical-card-ok', 'critical-card-warn', 'critical-card-bad'];
const STATS_STATE_STORAGE_KEY = 'iris.statistics.state';
const STATS_AUTO_REFRESH_INTERVAL_MS = 60000;

let statsAutoRefreshTimer = null;
let currentQuickRangeKey = null;
let currentQuickRangeOptions = null;
let statsIsRestoring = false;

const statsCharts = {
  alertsSeverity: null,
  alertsStatus: null,
  caseClassifications: null,
  caseStatus: null,
  evidences: null,
  caseSeverity: null,
  queueAlertStatus: null
};

function formatDateForInput(date) {
  if (!(date instanceof Date) || Number.isNaN(date)) {
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
  if (Number.isNaN(parsed)) {
    return null;
  }

  return parsed;
}

function toUtcIso(date) {
  if (!(date instanceof Date) || Number.isNaN(date)) {
    return null;
  }
  return date.toISOString();
}

function parseServerDate(value) {
  if (!value) {
    return null;
  }

  let normalized = value;
  if (!/[zZ]|([+-]\d{2}:?\d{2})$/.test(value)) {
    normalized = `${value}Z`;
  }

  const parsed = new Date(normalized);
  if (Number.isNaN(parsed)) {
    return null;
  }

  return parsed;
}

function setStatsLoading(isLoading) {
  const loader = $('#statsLoadingIndicator');
  if (loader.length) {
    loader.toggleClass('d-none', !isLoading);
  }
  $('#statsApplyBtn').prop('disabled', isLoading);
  $('#statsResetBtn').prop('disabled', isLoading);
  $('.stats-quick-range').prop('disabled', isLoading);
}

function formatDurationMetric(metric) {
  if (!metric || metric.seconds === null || metric.seconds === undefined) {
    return { primary: 'N/A', secondary: '--' };
  }

  const totalSeconds = Number(metric.seconds);
  const totalMinutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  const totalHours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  const hours = totalHours % 24;
  const days = Math.floor(totalHours / 24);

  const parts = [];
  if (days) {
    parts.push(`${days}d`);
  }
  if (hours) {
    parts.push(`${hours}h`);
  }
  if (minutes) {
    parts.push(`${minutes}m`);
  }
  if (!parts.length && seconds) {
    parts.push(`${seconds}s`);
  }
  if (!parts.length) {
    parts.push('0m');
  }

  const secondary = metric.hours !== null && metric.hours !== undefined
    ? `≈ ${Number(metric.hours).toFixed(2)} h`
    : '--';

  return {
    primary: parts.join(' '),
    secondary
  };
}

function formatCountValue(value) {
  if (value === null || value === undefined) {
    return '--';
  }

  const numericValue = Number(value);
  if (Number.isNaN(numericValue)) {
    return '--';
  }

  return numericValue.toLocaleString();
}

function formatPercentValue(value) {
  if (value === null || value === undefined) {
    return 'N/A';
  }

  const numericValue = Number(value);
  if (Number.isNaN(numericValue)) {
    return 'N/A';
  }

  return `${numericValue.toFixed(1)}%`;
}

function formatSizeValue(bytes) {
  if (bytes === null || bytes === undefined) {
    return '--';
  }

  const numericValue = Number(bytes);
  if (Number.isNaN(numericValue)) {
    return '--';
  }

  if (numericValue === 0) {
    return '0 B';
  }

  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const exponent = Math.min(Math.floor(Math.log(numericValue) / Math.log(1024)), units.length - 1);
  const value = numericValue / Math.pow(1024, exponent);
  const decimals = value >= 10 || exponent === 0 ? 0 : 1;
  return `${value.toFixed(decimals)} ${units[exponent]}`;
}

function updateDurationCard(primaryId, secondaryId, metric) {
  const duration = formatDurationMetric(metric);
  $(`#${primaryId}`).text(duration.primary);
  $(`#${secondaryId}`).text(duration.secondary);
}

function updateTimeframeSummary(timeframe) {
  const summary = $('#statsSummaryRange');
  if (!summary.length) {
    return;
  }

  if (!timeframe || !timeframe.start || !timeframe.end) {
    summary.text('--');
    return;
  }

  const start = parseServerDate(timeframe.start);
  const end = parseServerDate(timeframe.end);

  if (!start || !end) {
    summary.text('--');
    return;
  }

  summary.text(`${start.toLocaleString()} – ${end.toLocaleString()}`);
}

function updateAlertsSection(payload) {
  const metrics = payload && payload.metrics ? payload.metrics : {};
  const alerts = payload && payload.alerts ? payload.alerts : {};

  $('#alertsSummaryTotal').text(alerts.total !== null && alerts.total !== undefined ? `Total alerts: ${formatCountValue(alerts.total)}` : '--');

  updateDurationCard('alertsMttdPrimary', 'alertsMttdSecondary', metrics.mean_time_to_detect);
  $('#alertsDetectionCoverage').text(formatPercentValue(metrics.detection_coverage_percent));
  $('#alertsEscalationRate').text(formatPercentValue(metrics.incident_escalation_rate_percent));

  $('#alertsTotal').text(formatCountValue(alerts.total));
  $('#alertsFalsePositive').text(formatCountValue(alerts.false_positive_count));
  $('#alertsAssociated').text(formatCountValue(alerts.associated_with_cases));

  const totalLabel = alerts.total !== null && alerts.total !== undefined ? `Total alerts: ${formatCountValue(alerts.total)}` : '--';
  $('#alertsSeverityTotal').text(totalLabel);
  updateDistributionChart(alerts.severity_distribution, '#alertsSeverityChart', '#alertsSeverityEmpty', 'alertsSeverity', 'severity');

  $('#alertsStatusTotal').text(totalLabel);
  updateDistributionChart(alerts.status_distribution, '#alertsStatusChart', '#alertsStatusEmpty', 'alertsStatus', 'status');
}

function updateCasesMetrics(metrics) {
  if (!metrics) {
    updateDurationCard('casesMttrPrimary', 'casesMttrSecondary', null);
    $('#casesIncidentsResolved').text('--');
    $('#casesIncidentsDetected').text('--');
    $('#casesFalsePositives').text('--');
    $('#casesFalsePositiveRate').text('N/A');
    $('#casesSummaryTotal').text('--');
    updateCaseSeverityChart([], null);
    return;
  }

  updateDurationCard('casesMttrPrimary', 'casesMttrSecondary', metrics.mean_time_to_respond);

  $('#casesIncidentsResolved').text(formatCountValue(metrics.incidents_resolved));
  $('#casesIncidentsDetected').text(formatCountValue(metrics.incidents_detected));
  $('#casesFalsePositives').text(formatCountValue(metrics.false_positive_incidents));
  $('#casesFalsePositiveRate').text(formatPercentValue(metrics.false_positive_rate_percent));

  const summaryParts = [];
  if (metrics.incidents_detected !== null && metrics.incidents_detected !== undefined) {
    summaryParts.push(`Detected: ${formatCountValue(metrics.incidents_detected)}`);
  }
  if (metrics.incidents_resolved !== null && metrics.incidents_resolved !== undefined) {
    summaryParts.push(`Resolved: ${formatCountValue(metrics.incidents_resolved)}`);
  }
  $('#casesSummaryTotal').text(summaryParts.length ? summaryParts.join(' | ') : '--');

  updateCaseSeverityChart(metrics.case_severity_distribution, metrics.incidents_detected);
}

function updateCaseSeverityChart(rows, total) {
  $('#casesSeverityTotal').text(total !== null && total !== undefined ? `Incidents detected: ${formatCountValue(total)}` : '--');

  const emptyMessage = $('#casesSeverityEmpty');
  const chartCanvas = $('#casesSeverityChart');

  if (!emptyMessage.length || !chartCanvas.length) {
    return;
  }

  const items = Array.isArray(rows) ? rows.slice() : [];

  if (!items.length) {
    emptyMessage.show();
    chartCanvas.addClass('d-none');
    if (statsCharts.caseSeverity) {
      statsCharts.caseSeverity.destroy();
      statsCharts.caseSeverity = null;
    }
    return;
  }

  emptyMessage.hide();

  const chartItems = items.filter((item) => (Number(item.count) || 0) > 0);

  if (!chartItems.length) {
    chartCanvas.addClass('d-none');
    if (statsCharts.caseSeverity) {
      statsCharts.caseSeverity.destroy();
      statsCharts.caseSeverity = null;
    }
    return;
  }

  const labels = chartItems.map((item) => item.severity || 'Unspecified');
  const values = chartItems.map((item) => Number(item.count) || 0);
  const percentages = chartItems.map((item) => (item.percentage !== null && item.percentage !== undefined ? Number(item.percentage) : null));

  chartCanvas.removeClass('d-none');
  statsCharts.caseSeverity = renderDoughnutChart(chartCanvas[0], statsCharts.caseSeverity, labels, values, percentages, {
    legendPosition: 'right'
  });
}

function getChartColors(count) {
  const colors = [];
  for (let i = 0; i < count; i += 1) {
    colors.push(STATS_CHART_COLORS[i % STATS_CHART_COLORS.length]);
  }
  return colors;
}

function hexToRgba(hex, alpha) {
  if (!hex || typeof hex !== 'string') {
    return `rgba(23, 125, 255, ${alpha})`;
  }

  const sanitized = hex.replace('#', '');
  if (sanitized.length !== 6) {
    return `rgba(23, 125, 255, ${alpha})`;
  }

  const value = parseInt(sanitized, 16);
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function renderDoughnutChart(canvas, existingChart, labels, values, percentages, options = {}) {
  if (!canvas) {
    return null;
  }

  const context = canvas.getContext('2d');
  if (!context) {
    return null;
  }

  if (existingChart) {
    existingChart.destroy();
  }

  const legendDisplay = Object.prototype.hasOwnProperty.call(options, 'legendDisplay') ? options.legendDisplay : true;
  const legendPosition = options.legendPosition || 'bottom';

  return new Chart(context, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: getChartColors(values.length),
        borderWidth: 1
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      legend: {
        display: legendDisplay,
        position: legendPosition
      },
      tooltips: {
        callbacks: {
          label: function (tooltipItem, data) {
            const dataset = data.datasets[tooltipItem.datasetIndex];
            const value = dataset.data[tooltipItem.index];
            const label = data.labels[tooltipItem.index] || '';
            const percentValue = percentages && percentages[tooltipItem.index] !== null && percentages[tooltipItem.index] !== undefined
              ? `${Number(percentages[tooltipItem.index]).toFixed(1)}%`
              : null;

            if (percentValue) {
              return `${label}: ${value} (${percentValue})`;
            }
            return `${label}: ${value}`;
          }
        }
      }
    }
  });
}

function updateDistributionChart(distribution, canvasSelector, emptySelector, chartKey, labelKey, config = {}) {
  const canvas = $(canvasSelector);
  const emptyMessage = $(emptySelector);

  if (!canvas.length || !emptyMessage.length) {
    return;
  }

  if (config.fixedHeight) {
    canvas.attr('height', config.fixedHeight);
  }

  const items = Array.isArray(distribution) ? distribution.filter((item) => item && (Number(item.count) || 0) > 0) : [];

  if (!items.length) {
    emptyMessage.show();
    canvas.addClass('d-none');
    if (statsCharts[chartKey]) {
      statsCharts[chartKey].destroy();
      statsCharts[chartKey] = null;
    }
    if (config.legendContainerSelector) {
      const legendContainer = $(config.legendContainerSelector);
      if (legendContainer.length) {
        legendContainer.addClass('d-none').empty();
      }
    }
    return;
  }

  emptyMessage.hide();
  canvas.removeClass('d-none');

  const labels = items.map((item) => item[labelKey] || 'Unspecified');
  const values = items.map((item) => Number(item.count) || 0);
  const percentages = items.map((item) => (item.percentage !== null && item.percentage !== undefined ? Number(item.percentage) : null));

  const legendDisplay = config.legendContainerSelector ? false : config.legendDisplay;
  statsCharts[chartKey] = renderDoughnutChart(canvas[0], statsCharts[chartKey], labels, values, percentages, {
    legendDisplay: legendDisplay !== undefined ? legendDisplay : true,
    legendPosition: config.legendPosition || 'bottom'
  });

  if (config.legendContainerSelector) {
    const legendContainer = $(config.legendContainerSelector);
    if (legendContainer.length) {
      const legendHtml = statsCharts[chartKey] ? statsCharts[chartKey].generateLegend() : '';
      if (legendHtml) {
        legendContainer.removeClass('d-none').html(legendHtml);
      } else {
        legendContainer.addClass('d-none').empty();
      }
    }
  }
}

function renderLineTimeSeriesChart(canvas, existingChart, datasets, options = {}) {
  if (!canvas) {
    return null;
  }

  const context = canvas.getContext('2d');
  if (!context) {
    return null;
  }

  if (existingChart) {
    existingChart.destroy();
  }

  return new Chart(context, {
    type: 'line',
    data: {
      datasets
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      legend: {
        position: 'bottom'
      },
      tooltips: {
        mode: 'index',
        intersect: false
      },
      scales: {
        xAxes: [{
          type: 'time',
          time: {
            unit: options.timeUnit || 'day'
          },
          distribution: 'series',
          ticks: {
            maxRotation: 0,
            autoSkip: true
          }
        }],
        yAxes: [{
          ticks: {
            beginAtZero: true,
            precision: 0
          }
        }]
      },
      elements: {
        line: {
          tension: 0.2,
          borderWidth: 2
        },
        point: {
          radius: 0,
          hitRadius: 6,
          hoverRadius: 4
        }
      }
    }
  });
}

function updateQueueTimeSeries(timeSeries) {
  const summary = $('#queueSummaryRange');
  const canvas = $('#queueAlertStatusChart');
  const emptyMessage = $('#queueAlertStatusEmpty');

  if (!summary.length || !canvas.length || !emptyMessage.length) {
    return;
  }

  let summaryText = '--';

  if (!timeSeries || !Array.isArray(timeSeries.points) || !timeSeries.points.length) {
    if (timeSeries && timeSeries.start && timeSeries.end) {
      const start = parseServerDate(timeSeries.start);
      const end = parseServerDate(timeSeries.end);
      if (start && end) {
        summaryText = `${start.toLocaleString()} – ${end.toLocaleString()}`;
      }
    }

    summary.text(summaryText);
    emptyMessage.show();
    canvas.addClass('d-none');
    if (statsCharts.queueAlertStatus) {
      statsCharts.queueAlertStatus.destroy();
      statsCharts.queueAlertStatus = null;
    }
    return;
  }

  const start = parseServerDate(timeSeries.start);
  const end = parseServerDate(timeSeries.end);
  if (start && end) {
    summaryText = `${start.toLocaleString()} – ${end.toLocaleString()}`;
  }

  const createdData = [];
  const handledData = [];

  timeSeries.points.forEach((point) => {
    if (!point || !point.date) {
      return;
    }

    const parsedDate = parseServerDate(point.date);
    if (!parsedDate) {
      return;
    }

    const createdCount = Number(point.created);
    const handledCount = Number(point.handled);

    createdData.push({
      x: parsedDate,
      y: Number.isFinite(createdCount) ? createdCount : 0
    });

    handledData.push({
      x: parsedDate,
      y: Number.isFinite(handledCount) ? handledCount : 0
    });
  });

  createdData.sort((a, b) => a.x - b.x);
  handledData.sort((a, b) => a.x - b.x);

  if (!createdData.length && !handledData.length) {
    summary.text(summaryText);
    emptyMessage.show();
    canvas.addClass('d-none');
    if (statsCharts.queueAlertStatus) {
      statsCharts.queueAlertStatus.destroy();
      statsCharts.queueAlertStatus = null;
    }
    return;
  }

  const createdColor = STATS_CHART_COLORS[0];
  const handledColor = STATS_CHART_COLORS[3];

  const datasets = [
    {
      label: 'Created alerts',
      data: createdData,
      borderColor: createdColor,
      backgroundColor: hexToRgba(createdColor, 0.2),
      fill: false,
      lineTension: 0,
      pointRadius: 0,
      pointHitRadius: 6,
      pointHoverRadius: 4,
      spanGaps: true
    },
    {
      label: 'Handled alerts',
      data: handledData,
      borderColor: handledColor,
      backgroundColor: hexToRgba(handledColor, 0.2),
      fill: false,
      lineTension: 0,
      pointRadius: 0,
      pointHitRadius: 6,
      pointHoverRadius: 4,
      spanGaps: true
    }
  ];

  summary.text(summaryText);
  emptyMessage.hide();
  canvas.removeClass('d-none');

  const timeUnit = timeSeries.time_unit || 'day';

  statsCharts.queueAlertStatus = renderLineTimeSeriesChart(
    canvas[0],
    statsCharts.queueAlertStatus,
    datasets,
    { timeUnit }
  );
}

function setQueueCountText(selector, value) {
  const element = $(selector);
  if (!element.length) {
    return;
  }

  element.text(formatCountValue(value));
}

function applyQueueCardState(cardSelector, value, thresholds) {
  const card = $(cardSelector);
  if (!card.length) {
    return;
  }

  const classes = QUEUE_CARD_CLASSES.join(' ');
  card.removeClass(classes);

  if (value === null || value === undefined) {
    return;
  }

  const numericValue = Number(value);
  if (Number.isNaN(numericValue)) {
    return;
  }

  if (!thresholds || (thresholds.ok === undefined && thresholds.warn === undefined)) {
    return;
  }

  const okThreshold = thresholds.ok !== undefined ? Number(thresholds.ok) : null;
  const warnThreshold = thresholds.warn !== undefined ? Number(thresholds.warn) : okThreshold;

  if (okThreshold === null || Number.isNaN(okThreshold) || warnThreshold === null || Number.isNaN(warnThreshold)) {
    return;
  }

  if (numericValue <= okThreshold) {
    card.addClass('critical-card-ok');
    return;
  }

  if (numericValue <= warnThreshold) {
    card.addClass('critical-card-warn');
    return;
  }

  card.addClass('critical-card-bad');
}

function updateQueueSection(queue) {
  const summaryElement = $('#queueSummaryRange');
  let summaryText = '--';

  if (queue && queue.summary && queue.summary.start && queue.summary.end) {
    const start = parseServerDate(queue.summary.start);
    const end = parseServerDate(queue.summary.end);
    if (start && end) {
      summaryText = `${start.toLocaleString()} – ${end.toLocaleString()}`;
    }
  }

  if (summaryElement.length) {
    summaryElement.text(summaryText);
  }

  const thresholds = queue && queue.thresholds ? queue.thresholds : {};

  const unassignedTotal = queue ? queue.unassigned_alerts_total : null;
  setQueueCountText('#queueUnassignedAlerts', unassignedTotal);
  applyQueueCardState('#queueUnassignedAlertsCard', unassignedTotal, thresholds.unassigned_alerts_total);

  const newAlertsTotal = queue ? queue.new_alerts_total : null;
  setQueueCountText('#queueNewAlertsTotal', newAlertsTotal);
  applyQueueCardState('#queueNewAlertsTotalCard', newAlertsTotal, thresholds.new_alerts_total);

  const windowOrder = queue && Array.isArray(queue.window_order) && queue.window_order.length
    ? queue.window_order
    : ['2h', '24h', '48h'];

  const newAlertsWindows = queue && queue.new_alerts_windows ? queue.new_alerts_windows : {};
  const newUnassignedWindows = queue && queue.new_unassigned_alerts_windows ? queue.new_unassigned_alerts_windows : {};

  let newAlertsMax = null;
  windowOrder.forEach((key) => {
    setQueueCountText(`#queueNewAlerts${key}`, newAlertsWindows[key]);
    const numericValue = Number(newAlertsWindows[key]);
    if (!Number.isNaN(numericValue)) {
      newAlertsMax = newAlertsMax === null ? numericValue : Math.max(newAlertsMax, numericValue);
    }
  });

  let newUnassignedMax = null;
  windowOrder.forEach((key) => {
    setQueueCountText(`#queueNewUnassigned${key}`, newUnassignedWindows[key]);
    const numericValue = Number(newUnassignedWindows[key]);
    if (!Number.isNaN(numericValue)) {
      newUnassignedMax = newUnassignedMax === null ? numericValue : Math.max(newUnassignedMax, numericValue);
    }
  });

  applyQueueCardState('#queueNewAlertsRecentCard', newAlertsMax, thresholds.new_alerts_windows);
  applyQueueCardState('#queueNewUnassignedRecentCard', newUnassignedMax, thresholds.new_unassigned_alerts_windows);

  updateQueueTimeSeries(queue ? queue.time_series : null);
}

function updateCaseCharts(payload) {
  const total = payload ? payload.total_cases : null;
  const totalLabel = total !== null && total !== undefined ? `Total cases: ${formatCountValue(total)}` : '--';

  $('#casesClassificationTotal').text(totalLabel);
  updateDistributionChart(payload ? payload.classifications : [], '#casesClassificationChart', '#casesClassificationEmpty', 'caseClassifications', 'classification', {
    legendContainerSelector: '#casesClassificationLegend',
    legendPosition: 'right',
    fixedHeight: 260
  });

  $('#casesStatusTotal').text(totalLabel);
  updateDistributionChart(payload ? payload.status_distribution : [], '#casesStatusChart', '#casesStatusEmpty', 'caseStatus', 'status');
}

function updateCasesEvidenceChart(payload) {
  const total = payload ? payload.total_evidences : null;
  const totalSize = payload ? payload.total_size_bytes : null;
  $('#casesEvidenceSummary').text(
    total !== null && total !== undefined
      ? `Total: ${formatCountValue(total)} | Size: ${formatSizeValue(totalSize)}`
      : '--'
  );

  const rows = payload && Array.isArray(payload.evidence_types) ? payload.evidence_types : [];
  const emptyMessage = $('#casesEvidenceEmpty');
  const canvas = $('#casesEvidenceChart');

  if (!rows.length) {
    emptyMessage.show();
    canvas.addClass('d-none');
    if (statsCharts.evidences) {
      statsCharts.evidences.destroy();
      statsCharts.evidences = null;
    }
    return;
  }

  emptyMessage.hide();
  canvas.removeClass('d-none');

  const labels = rows.map((row) => row.type || 'Unspecified');
  const values = rows.map((row) => Number(row.count) || 0);
  const percentages = rows.map((row) => (row.percentage !== null && row.percentage !== undefined ? Number(row.percentage) : null));

  statsCharts.evidences = renderDoughnutChart(canvas[0], statsCharts.evidences, labels, values, percentages, {
    legendPosition: 'right'
  });
}

function populateCaseStatusOptions() {
  const select = $('#statsCaseStatus');
  if (!select.length) {
    return;
  }

  select.empty();
  STATS_CASE_STATUS_OPTIONS.forEach((option) => {
    select.append(new Option(option.label, option.id, false, false));
  });

  if ($.fn.select2) {
    select.select2({
      allowClear: true,
      placeholder: 'All statuses',
      width: 'resolve'
    });
  }
}

function loadSeverityOptions() {
  const select = $('#statsSeverity');
  if (!select.length) {
    return $.Deferred().resolve().promise();
  }

  select.find('option:not([value=""])').remove();

  return get_request_api('/manage/severities/list')
    .done((data) => {
      if (!notify_auto_api(data, true)) {
        return;
      }

      const severities = data.data || [];
      severities.sort((a, b) => a.severity_name.localeCompare(b.severity_name));
      severities.forEach((severity) => {
        select.append(new Option(severity.severity_name, severity.severity_id));
      });
    })
    .fail((xhr) => {
      ajax_notify_error(xhr, '/manage/severities/list');
    })
    .always(() => {
      if ($.fn.select2) {
        select.select2({
          allowClear: true,
          placeholder: 'All severities',
          width: 'resolve'
        });
      }
    });
}

function loadClientOptions() {
  const select = $('#statsClient');
  if (!select.length) {
    return $.Deferred().resolve().promise();
  }

  select.find('option:not([value=""])').remove();

  return get_request_api('/manage/customers/list')
    .done((data) => {
      if (!notify_auto_api(data, true)) {
        return;
      }

      const customers = data.data || [];
      customers.sort((a, b) => a.customer_name.localeCompare(b.customer_name));
      customers.forEach((customer) => {
        select.append(new Option(customer.customer_name, customer.customer_id));
      });
    })
    .fail((xhr) => {
      if (xhr && xhr.status === 403) {
        select.prop('disabled', true);
        const label = select.closest('.form-group').find('label');
        if (label.length && !/no access$/i.test(label.text().trim())) {
          label.append(' (no access)');
        }
        return;
      }

      ajax_notify_error(xhr, '/manage/customers/list');
    })
    .always(() => {
      if ($.fn.select2) {
        select.select2({
          allowClear: true,
          placeholder: 'All customers',
          width: 'resolve'
        });
      }
    });
}

function loadStatsState() {
  if (typeof window === 'undefined' || !window.localStorage) {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(STATS_STATE_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    return JSON.parse(raw);
  } catch (error) {
    console.warn('Unable to load statistics state', error);
    return null;
  }
}

function saveStatsState(state) {
  if (typeof window === 'undefined' || !window.localStorage) {
    return;
  }

  try {
    if (!state) {
      window.localStorage.removeItem(STATS_STATE_STORAGE_KEY);
      return;
    }
    window.localStorage.setItem(STATS_STATE_STORAGE_KEY, JSON.stringify(state));
  } catch (error) {
    console.warn('Unable to persist statistics state', error);
  }
}

function getCurrentFiltersState() {
  const clientValue = $('#statsClient').val();
  const severityValue = $('#statsSeverity').val();
  const caseStatusValue = $('#statsCaseStatus').val();

  return {
    start: $('#statsStart').val() || null,
    end: $('#statsEnd').val() || null,
    clientId: clientValue !== undefined && clientValue !== null && clientValue !== '' ? String(clientValue) : null,
    severityId: severityValue !== undefined && severityValue !== null && severityValue !== '' ? String(severityValue) : null,
    caseStatusId: caseStatusValue !== undefined && caseStatusValue !== null && caseStatusValue !== '' ? String(caseStatusValue) : null,
    quickRangeKey: currentQuickRangeKey,
    autoRefresh: $('#statsAutoRefresh').is(':checked')
  };
}

function persistCurrentFilters() {
  if (statsIsRestoring) {
    return;
  }
  saveStatsState(getCurrentFiltersState());
}

function setQuickRangeActive(rangeKey) {
  const buttons = $('.stats-quick-range');
  if (!buttons.length) {
    return;
  }

  buttons.removeClass('active');

  if (!rangeKey) {
    return;
  }

  const activeButton = buttons.filter(`[data-range-key="${rangeKey}"]`);
  if (activeButton.length) {
    activeButton.addClass('active');
  }
}

function clearQuickRangeSelection(options = {}) {
  currentQuickRangeKey = null;
  currentQuickRangeOptions = null;
  setQuickRangeActive(null);

  if (!options.suppressPersist) {
    persistCurrentFilters();
  }
}

function extractQuickRangeOptions(button) {
  if (!button || !button.length) {
    return null;
  }

  const optionUnits = ['minutes', 'hours', 'days', 'months', 'years'];
  const options = {};

  optionUnits.forEach((unit) => {
    const raw = button.data(unit);
    if (raw !== undefined && raw !== null && raw !== '' && !Number.isNaN(Number(raw))) {
      options[unit] = Number(raw);
    }
  });

  return Object.keys(options).length ? options : null;
}

function computeQuickRangeStart(now, rangeOptions = {}) {
  const start = new Date(now);

  if (rangeOptions.years) {
    start.setFullYear(start.getFullYear() - rangeOptions.years);
  }
  if (rangeOptions.months) {
    start.setMonth(start.getMonth() - rangeOptions.months);
  }
  if (rangeOptions.days) {
    start.setDate(start.getDate() - rangeOptions.days);
  }
  if (rangeOptions.hours) {
    start.setHours(start.getHours() - rangeOptions.hours);
  }
  if (rangeOptions.minutes) {
    start.setMinutes(start.getMinutes() - rangeOptions.minutes);
  }

  return start;
}

function applyQuickRangeToInputs(rangeOptions) {
  if (!rangeOptions) {
    return;
  }

  const now = new Date();
  const start = computeQuickRangeStart(now, rangeOptions);
  $('#statsEnd').val(formatDateForInput(now));
  $('#statsStart').val(formatDateForInput(start));
}

function setQuickRange(rangeKey, rangeOptions, options = {}) {
  currentQuickRangeKey = rangeKey || null;
  currentQuickRangeOptions = rangeOptions || null;
  setQuickRangeActive(currentQuickRangeKey);

  if (currentQuickRangeOptions) {
    applyQuickRangeToInputs(currentQuickRangeOptions);
  }

  const shouldPersist = options.persist !== false;
  if (shouldPersist) {
    persistCurrentFilters();
  }

  if (options.shouldFetch !== false) {
    fetchStatisticsData();
  }
}

function clearAutoRefreshTimer() {
  if (statsAutoRefreshTimer) {
    clearInterval(statsAutoRefreshTimer);
    statsAutoRefreshTimer = null;
  }
}

function setAutoRefresh(enabled, options = {}) {
  const checkbox = $('#statsAutoRefresh');
  if (checkbox.length) {
    checkbox.prop('checked', !!enabled);
  }

  clearAutoRefreshTimer();

  if (enabled) {
    const intervalCandidate = options.interval !== undefined ? Number(options.interval) : STATS_AUTO_REFRESH_INTERVAL_MS;
    const intervalMs = !Number.isNaN(intervalCandidate) && intervalCandidate > 0 ? intervalCandidate : STATS_AUTO_REFRESH_INTERVAL_MS;
    statsAutoRefreshTimer = setInterval(() => {
      fetchStatisticsData();
    }, intervalMs);
  }

  if (options.persist !== false) {
    persistCurrentFilters();
  }

  if (enabled && options.skipFetch !== true) {
    fetchStatisticsData();
  }
}

function restoreStatsFilters() {
  statsIsRestoring = true;
  try {
    const state = loadStatsState();

    if (!state) {
      resetStatsFilters();
      setAutoRefresh(false, { skipFetch: true, persist: false });
      return;
    }

    const clientSelect = $('#statsClient');
    if (clientSelect.length) {
      clientSelect.val(state.clientId !== null && state.clientId !== undefined ? state.clientId : '').trigger('change');
    }

    const severitySelect = $('#statsSeverity');
    if (severitySelect.length) {
      severitySelect.val(state.severityId !== null && state.severityId !== undefined ? state.severityId : '').trigger('change');
    }

    const caseStatusSelect = $('#statsCaseStatus');
    if (caseStatusSelect.length) {
      caseStatusSelect.val(state.caseStatusId !== null && state.caseStatusId !== undefined ? state.caseStatusId : '').trigger('change');
    }

    let quickRangeApplied = false;
    if (state.quickRangeKey) {
      const button = $(`.stats-quick-range[data-range-key="${state.quickRangeKey}"]`);
      const rangeOptions = extractQuickRangeOptions(button);
      if (rangeOptions) {
        setQuickRange(state.quickRangeKey, rangeOptions, { shouldFetch: false, persist: false });
        quickRangeApplied = true;
      } else {
        clearQuickRangeSelection({ suppressPersist: true });
      }
    } else {
      clearQuickRangeSelection({ suppressPersist: true });
    }

    if (!quickRangeApplied) {
      $('#statsStart').val(state.start || '');
      $('#statsEnd').val(state.end || '');
    }

    const autoRefreshEnabled = Boolean(state.autoRefresh);
    setAutoRefresh(autoRefreshEnabled, { skipFetch: true, persist: false });
  } finally {
    statsIsRestoring = false;
    persistCurrentFilters();
  }
}

function resetStatsFilters() {
  const now = new Date();
  $('#statsEnd').val(formatDateForInput(now));
  const start = new Date(now.getTime() - STATS_DEFAULT_RANGE_DAYS * 24 * 60 * 60 * 1000);
  $('#statsStart').val(formatDateForInput(start));
  $('#statsClient').val('').trigger('change');
  $('#statsSeverity').val('').trigger('change');
  $('#statsCaseStatus').val('').trigger('change');
  clearQuickRangeSelection({ suppressPersist: true });
  persistCurrentFilters();
}

function buildStatisticsParams() {
  const params = {};

  const startValue = $('#statsStart').val();
  const endValue = $('#statsEnd').val();
  const startDate = parseInputDate(startValue);
  const endDate = parseInputDate(endValue);

  if (startDate) {
    params.start = toUtcIso(startDate);
  }
  if (endDate) {
    params.end = toUtcIso(endDate);
  }

  const clientId = $('#statsClient').val();
  if (clientId) {
    params.client_id = clientId;
  }

  const severityId = $('#statsSeverity').val();
  if (severityId) {
    params.severity_id = severityId;
  }

  const caseStatusId = $('#statsCaseStatus').val();
  if (caseStatusId !== undefined && caseStatusId !== null && caseStatusId !== '') {
    params.case_status_id = caseStatusId;
  }

  return {
    params,
    startDate,
    endDate
  };
}

function fetchStatisticsData() {
  if (!$('#statsPageRoot').length) {
    return;
  }

  if (currentQuickRangeKey && currentQuickRangeOptions) {
    applyQuickRangeToInputs(currentQuickRangeOptions);
  }

  const { params, startDate, endDate } = buildStatisticsParams();

  if (startDate && endDate && startDate > endDate) {
    notify_error('Start date must be earlier than end date');
    return;
  }

  persistCurrentFilters();

  setStatsLoading(true);

  const suffix = case_param();
  const kpiRequest = $.ajax({
    url: '/statistics/api/kpis' + suffix,
    type: 'GET',
    dataType: 'json',
    data: $.extend({}, params)
  })
    .done((data) => {
      if (!notify_auto_api(data, true)) {
        updateQueueSection(null);
        return;
      }
      if (!data.data) {
        updateQueueSection(null);
        return;
      }
      updateTimeframeSummary(data.data.timeframe);
      updateAlertsSection(data.data);
      updateCasesMetrics(data.data.metrics);
      updateQueueSection(data.data.queue || null);
    })
    .fail((xhr) => {
      updateQueueSection(null);
      if (xhr && xhr.status === 400 && xhr.responseJSON && xhr.responseJSON.message) {
        notify_error(xhr.responseJSON.message);
      } else {
        ajax_notify_error(xhr, '/statistics/api/kpis');
      }
    });

  const classificationRequest = $.ajax({
    url: '/statistics/api/classifications' + suffix,
    type: 'GET',
    dataType: 'json',
    data: $.extend({}, params)
  })
    .done((data) => {
      if (!notify_auto_api(data, true)) {
        return;
      }
      if (!data.data) {
        return;
      }
      updateCaseCharts(data.data);
    })
    .fail((xhr) => {
      if (xhr && xhr.status === 400 && xhr.responseJSON && xhr.responseJSON.message) {
        notify_error(xhr.responseJSON.message);
      } else {
        ajax_notify_error(xhr, '/statistics/api/classifications');
      }
    });

  const evidenceRequest = $.ajax({
    url: '/statistics/api/evidence' + suffix,
    type: 'GET',
    dataType: 'json',
    data: $.extend({}, params)
  })
    .done((data) => {
      if (!notify_auto_api(data, true)) {
        return;
      }
      if (!data.data) {
        return;
      }
      updateCasesEvidenceChart(data.data);
    })
    .fail((xhr) => {
      if (xhr && xhr.status === 400 && xhr.responseJSON && xhr.responseJSON.message) {
        notify_error(xhr.responseJSON.message);
      } else {
        ajax_notify_error(xhr, '/statistics/api/evidence');
      }
    });

  $.when(kpiRequest, classificationRequest, evidenceRequest).always(() => {
    setStatsLoading(false);
  });
}

function initStatsPage() {
  if (!$('#statsPageRoot').length) {
    return;
  }

  populateCaseStatusOptions();

  const loaders = $.when(loadSeverityOptions(), loadClientOptions());

  loaders.always(() => {
    restoreStatsFilters();
    fetchStatisticsData();
  });

  $('#statsFilterForm').on('submit', function (event) {
    event.preventDefault();
    fetchStatisticsData();
  });

  $('.stats-quick-range').on('click', function () {
    const button = $(this);
    const rangeKey = button.data('range-key') || null;
    const rangeOptions = extractQuickRangeOptions(button);

    if (!rangeOptions) {
      clearQuickRangeSelection();
      fetchStatisticsData();
      return;
    }

    setQuickRange(rangeKey, rangeOptions);
  });

  $('#statsResetBtn').on('click', function () {
    resetStatsFilters();
    fetchStatisticsData();
  });

  $('#statsAutoRefresh').on('change', function () {
    const enabled = $(this).is(':checked');
    setAutoRefresh(enabled);
  });

  $('#statsStart, #statsEnd').on('change', function () {
    if (statsIsRestoring) {
      return;
    }
    clearQuickRangeSelection({ suppressPersist: true });
    persistCurrentFilters();
  });

  $('#statsClient, #statsSeverity, #statsCaseStatus').on('change', function () {
    if (statsIsRestoring) {
      return;
    }
    persistCurrentFilters();
  });
}

$(document).ready(() => {
  initStatsPage();
});
