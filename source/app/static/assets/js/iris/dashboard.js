let UserReviewsTable;
let UserCasesTable;
let UserTaskTable;
const KPI_DEFAULT_RANGE_DAYS = 30;
const KPI_CASE_STATUS_OPTIONS = [
  { id: '', label: 'All statuses' },
  { id: 0, label: 'Unknown' },
  { id: 1, label: 'False Positive' },
  { id: 2, label: 'True Positive (Impact)' },
  { id: 3, label: 'Not Applicable' },
  { id: 4, label: 'True Positive (No Impact)' },
  { id: 5, label: 'Legitimate' }
];

function formatDateForInput(date) {
  if (!(date instanceof Date) || isNaN(date)) {
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
  if (isNaN(parsed)) {
    return null;
  }

  return parsed;
}

function toUtcIso(date) {
  if (!(date instanceof Date) || isNaN(date)) {
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
  if (isNaN(parsed)) {
    return null;
  }

  return parsed;
}

function setKpiLoading(isLoading) {
  const loadingIndicator = $('#kpiLoadingIndicator');
  if (!loadingIndicator.length) {
    return;
  }

  if (isLoading) {
    loadingIndicator.addClass('is-visible');
  } else {
    loadingIndicator.removeClass('is-visible');
  }
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

function updateKpiSummary(timeframe) {
  const summaryLabel = $('#kpiSummaryRange');
  if (!summaryLabel.length) {
    return;
  }

  if (!timeframe || !timeframe.start || !timeframe.end) {
    summaryLabel.text('No timeframe selected');
    return;
  }

  const start = parseServerDate(timeframe.start);
  const end = parseServerDate(timeframe.end);

  if (!start || !end) {
    summaryLabel.text('No timeframe selected');
    return;
  }

  summaryLabel.text(`${start.toLocaleString()} – ${end.toLocaleString()}`);
}

function updateKpiMetrics(metrics) {
  if (!metrics) {
    return;
  }

  const mttd = formatDurationMetric(metrics.mean_time_to_detect);
  $('#kpiMttdPrimary').text(mttd.primary);
  $('#kpiMttdSecondary').text(mttd.secondary);

  const mttr = formatDurationMetric(metrics.mean_time_to_respond);
  $('#kpiMttrPrimary').text(mttr.primary);
  $('#kpiMttrSecondary').text(mttr.secondary);

  const mttc = formatDurationMetric(metrics.mean_time_to_contain);
  $('#kpiMttcPrimary').text(mttc.primary);
  $('#kpiMttcSecondary').text(mttc.secondary);

  const mttrv = formatDurationMetric(metrics.mean_time_to_recover);
  $('#kpiMttrvPrimary').text(mttrv.primary);
  $('#kpiMttrvSecondary').text(mttrv.secondary);

  $('#kpiIncidentsDetected').text(formatCountValue(metrics.incidents_detected));
  $('#kpiIncidentsResolved').text(formatCountValue(metrics.incidents_resolved));
  $('#kpiFalsePositives').text(formatCountValue(metrics.false_positive_incidents));
  $('#kpiFalsePositiveRate').text(formatPercentValue(metrics.false_positive_rate_percent));
  $('#kpiDetectionCoverage').text(formatPercentValue(metrics.detection_coverage_percent));
  $('#kpiEscalationRate').text(formatPercentValue(metrics.incident_escalation_rate_percent));
}

function updateSeverityDistribution(rows) {
  const tbody = $('#kpiSeverityBody');
  if (!tbody.length) {
    return;
  }

  tbody.empty();

  if (!rows || !rows.length) {
    tbody.append('<tr><td colspan="3" class="text-center text-muted">No data available</td></tr>');
    return;
  }

  rows.forEach((row) => {
    const severity = sanitizeHTML(row.severity || 'Unspecified');
    const count = formatCountValue(row.count);
    const percent = formatPercentValue(row.percentage);
    tbody.append(`<tr><td>${severity}</td><td class="text-right">${count}</td><td class="text-right">${percent}</td></tr>`);
  });
}

function populateCaseStatusOptions() {
  const select = $('#kpiCaseStatus');
  if (!select.length) {
    return;
  }

  select.empty();
  KPI_CASE_STATUS_OPTIONS.forEach((option) => {
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
  const select = $('#kpiSeverity');
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
  const select = $('#kpiClient');
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

function resetKpiFilters() {
  const now = new Date();
  $('#kpiEnd').val(formatDateForInput(now));
  const start = new Date(now.getTime() - KPI_DEFAULT_RANGE_DAYS * 24 * 60 * 60 * 1000);
  $('#kpiStart').val(formatDateForInput(start));
  $('#kpiClient').val('').trigger('change');
  $('#kpiSeverity').val('').trigger('change');
  $('#kpiCaseStatus').val('').trigger('change');
}

function applyQuickRange(days) {
  const now = new Date();
  $('#kpiEnd').val(formatDateForInput(now));
  const start = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
  $('#kpiStart').val(formatDateForInput(start));
  fetchKpiMetrics();
}

function fetchKpiMetrics() {
  if (!$('#dashboardKpiPanel').length) {
    return;
  }

  setKpiLoading(true);

  const startValue = $('#kpiStart').val();
  const endValue = $('#kpiEnd').val();
  const startDate = parseInputDate(startValue);
  const endDate = parseInputDate(endValue);

  if (startDate && endDate && startDate > endDate) {
    setKpiLoading(false);
    notify_error('Start date must be earlier than end date');
    return;
  }

  const params = {};
  const startIso = toUtcIso(startDate);
  const endIso = toUtcIso(endDate);
  if (startIso) {
    params.start = startIso;
  }
  if (endIso) {
    params.end = endIso;
  }

  const clientId = $('#kpiClient').val();
  if (clientId) {
    params.client_id = clientId;
  }

  const severityId = $('#kpiSeverity').val();
  if (severityId) {
    params.severity_id = severityId;
  }

  const caseStatusId = $('#kpiCaseStatus').val();
  if (caseStatusId !== undefined && caseStatusId !== null && caseStatusId !== '') {
    params.case_status_id = caseStatusId;
  }

  const url = '/dashboard/kpis' + case_param();

  $.ajax({
    url: url,
    type: 'GET',
    dataType: 'json',
    data: params
  })
    .done((data) => {
      if (!notify_auto_api(data, true)) {
        return;
      }

      if (!data.data) {
        return;
      }

      updateKpiSummary(data.data.timeframe);
      updateKpiMetrics(data.data.metrics);
      updateSeverityDistribution(data.data.metrics ? data.data.metrics.severity_distribution : []);
    })
    .fail((xhr) => {
      if (xhr && xhr.status === 400 && xhr.responseJSON && xhr.responseJSON.message) {
        notify_error(xhr.responseJSON.message);
      } else {
        ajax_notify_error(xhr, url);
      }
    })
    .always(() => {
      setKpiLoading(false);
    });
}

function initKpiPanel() {
  if (!$('#dashboardKpiPanel').length) {
    return;
  }

  populateCaseStatusOptions();

  $.when(loadSeverityOptions(), loadClientOptions()).always(() => {
    if ($.fn.select2) {
      $('#kpiSeverity').trigger('change');
      $('#kpiClient').trigger('change');
    }
  });

  resetKpiFilters();

  $('#kpiFilterForm').on('submit', function (event) {
    event.preventDefault();
    fetchKpiMetrics();
  });

  $('.kpi-quick-range').on('click', function () {
    const days = Number($(this).data('days'));
    if (!Number.isNaN(days)) {
      applyQuickRange(days);
    }
  });

  $('#kpiResetBtn').on('click', function () {
    resetKpiFilters();
    fetchKpiMetrics();
  });

  fetchKpiMetrics();
}

function check_page_update(){
    update_gtasks_list();
  initKpiPanel();

  update_utasks_list();
}

function task_status(id) {
    url = 'tasks/status/human/'+id + case_param();
    $('#info_task_modal_body').load(url, function (response, status, xhr) {
        if (status !== "success") {
             ajax_notify_error(xhr, url);
             return false;
        }
        $('#modal_task_detail').modal({show:true});
    });
}

async function update_ucases_list(show_all=false) {
    $('#ucases_list').empty();
    get_raw_request_api("/user/cases/list" + case_param() + "&show_closed=" + show_all)
    .done((data) => {
        if (notify_auto_api(data, true)) {
            UserCasesTable.clear();
            UserCasesTable.rows.add(data.data);
            UserCasesTable.columns.adjust().draw();
            UserCasesTable.buttons().container().appendTo($('#ucases_table_info'));
            $('[data-toggle="popover"]').popover();
            $('#ucases_last_updated').text("Last updated: " + new Date().toLocaleTimeString());
        }
    });
}

async function update_ureviews_list() {
    get_raw_request_api("/user/reviews/list" + case_param())
    .done((data) => {
        if (notify_auto_api(data, true)) {
            if (data.data.length == 0) {
                $('#rowPendingCasesReview').hide();
                return;
            }
            UserReviewsTable.clear();
            UserReviewsTable.rows.add(data.data);
            UserReviewsTable.columns.adjust().draw();
            $('[data-toggle="popover"]').popover();
            $('#ureviews_last_updated').text("Last updated: " + new Date().toLocaleTimeString());
            $('#rowPendingCasesReview').show();
        }
    });
}

async function update_utasks_list() {
    $('#utasks_list').empty();
    return get_request_api("/user/tasks/list")
    .done((data) => {
        if (notify_auto_api(data, true)) {
            UserTaskTable.MakeCellsEditable("destroy");
            tasks_list = data.data.tasks;

            $('#user_attr_count').text(tasks_list.length);
            if (tasks_list.length != 0){
                $('#icon_user_task').removeClass().addClass('flaticon-alarm text-danger');
            } else {
                $('#icon_user_task').removeClass().addClass('flaticon-success text-success');
            }
            options_l = data.data.tasks_status;
            options = [];
            for (index in options_l) {
                option = options_l[index];
                options.push({ "value": option.id, "display": option.status_name })
            }

            UserTaskTable.clear();
            UserTaskTable.rows.add(tasks_list);
            UserTaskTable.MakeCellsEditable({
                "onUpdate": callBackEditUserTaskStatus,
                "inputCss": 'form-control col-12',
                "columns": [2],
                "allowNulls": {
                  "columns": [2],
                  "errorClass": 'error'
                },
                "confirmationButton": {
                  "confirmCss": 'my-confirm-class',
                  "cancelCss": 'my-cancel-class'
                },
                "inputTypes": [
                  {
                    "column": 2,
                    "type": "list",
                    "options": options
                  }
                ]
              });

            UserTaskTable.columns.adjust().draw();
            UserTaskTable.buttons().container().appendTo($('#utasks_table_info'));
            $('[data-toggle="popover"]').popover();

            $('#utasks_last_updated').text("Last updated: " + new Date().toLocaleTimeString());
        }

    });
}

function callBackEditUserTaskStatus(updatedCell, updatedRow, oldValue) {
    data_send = updatedRow.data()
    data_send['csrf_token'] = $('#csrf_token').val();
    post_request_api("user/tasks/status/update", JSON.stringify(data_send))
    .done((data) => {
        if (notify_auto_api(data)) {
           update_utasks_list();
           UserTaskTable.columns.adjust().draw();
        }
    });
}


/**** GTASKS ****/

/* Fetch a modal that allows to add an event */
function add_gtask() {
    url = '/global/tasks/add/modal' + case_param();
    $('#modal_add_gtask_content').load(url, function (response, status, xhr) {
        if (status !== "success") {
             ajax_notify_error(xhr, url);
             return false;
        }

        $('#submit_new_gtask').on("click", function () {
            var data_sent = $('#form_new_gtask').serializeObject();
            data_sent['task_tags'] = $('#task_tags').val();
            data_sent['task_assignees_id'] = $('#task_assignees_id').val();
            data_sent['task_status_id'] = $('#task_status_id').val();
            data_sent['csrf_token'] = $('#csrf_token').val();

            post_request_api('/global/tasks/add', JSON.stringify(data_sent), true)
            .done((data) => {
                if(notify_auto_api(data)) {
                    update_gtasks_list();
                    $('#modal_add_gtask').modal('hide');
                }
            });

            return false;
        })

    });

    $('#modal_add_gtask').modal({ show: true });
}

function update_gtask(id) {
    var data_sent = $('#form_new_gtask').serializeObject();
    data_sent['task_tags'] = $('#task_tags').val();
    data_sent['task_assignee_id'] = $('#task_assignee_id').val();
    data_sent['task_status_id'] = $('#task_status_id').val();
    data_sent['csrf_token'] = $('#csrf_token').val();

    post_request_api('/global/tasks/update/' + id, JSON.stringify(data_sent), true)
    .done((data) => {
        if(notify_auto_api(data)) {
            update_gtasks_list();
            $('#modal_add_gtask').modal('hide');
        }
    });
}

/* Delete an event from the timeline thank to its id */
function delete_gtask(id) {
    post_request_api("/global/tasks/delete/" + id)
    .done((data) => {
        if(notify_auto_api(data)) {
            update_gtasks_list();
            $('#modal_add_gtask').modal('hide');
        }
    });
}

/* Edit and event from the timeline thanks to its ID */
function edit_gtask(id) {
  url = '/global/tasks/update/'+ id + "/modal" + case_param();
  $('#modal_add_gtask_content').load(url, function (response, status, xhr) {
        if (status !== "success") {
             ajax_notify_error(xhr, url);
             return false;
        }
        $('#modal_add_gtask').modal({show:true});
  });
}


/* Fetch and draw the tasks */
async function update_gtasks_list() {
    $('#gtasks_list').empty();

    return get_request_api("/global/tasks/list")
    .done((data) => {
        if(notify_auto_api(data, true)) {
            Table.MakeCellsEditable("destroy");
            tasks_list = data.data.tasks;

            options_l = data.data.tasks_status;
            options = [];
            for (index in options_l) {
                option = options_l[index];
                options.push({ "value": option.id, "display": option.status_name })
            }

            Table.clear();
            Table.rows.add(tasks_list);

            Table.columns.adjust().draw();
            Table.buttons().container().appendTo($('#gtasks_table_info'));
               $('[data-toggle="popover"]').popover();

            load_menu_mod_options('global_task', Table, delete_gtask);
            $('#tasks_last_updated').text("Last updated: " + new Date().toLocaleTimeString());
        }
    });
}


$(document).ready(function() {

        UserReviewsTable = $("#ureview_table").DataTable({
            dom: 'frtip',
            aaData: [],
            aoColumns: [
              {
                  "data": "name",
                  "render": function (data, type, row, meta) {
                    if (type === 'display') {
                        data = `<a  href="/case?cid=${row['case_id']}">${sanitizeHTML(data)}</a>`;
                    }
                    return data;
                    }
                },
                {
                    "data": "status_name",
                    "render": function (data, type, row, meta) {
                        if (type === 'display') {
                            data = `<span class="badge badge-light">${sanitizeHTML(data)}</span>`;
                        }
                        return data;
                    }
                }
            ],
            ordering: false,
            processing: true,
            retrieve: true,
            lengthChange: false,
            pageLength: 10,
            order: [[ 1, "asc" ]],
            select: true
        });

        UserCasesTable = $("#ucases_table").DataTable({
            dom: 'Blfrtip',
            aaData: [],
            aoColumns: [
              {
                "data": "name",
                "render": function (data, type, row, meta) {
                  if (type === 'display') {
                      let a_anchor = $('<a>');
                        a_anchor.attr('href', '/case?cid='+ row['case_id']);
                        a_anchor.attr('target', '_blank');
                        a_anchor.attr('rel', 'noopener');
                        a_anchor.title="Go to case";
                        a_anchor.text(data);
                    return a_anchor[0].outerHTML;
                  }
                  return data;
                }
              },
              {
                 "data": "description",
                  "render": function (data, type, row, meta) {
                    if (type === 'display') {
                        return ret_obj_dt_description(data);
                  }
                  return data;
                }
              },
              {
                "data": "client",
                "render": function(data, type, row, meta) {
                   if (type === 'display') {
                      //data = sanitizeHTML(data);
                      data = sanitizeHTML(row['client']['customer_name']);
                  }
                  return data;
                }
              },
              {
                "data": "open_date",
                "render": function (data, type, row, meta) {
                    if (type === 'display') {
                        data = sanitizeHTML(data);
                    }
                    return data;
                  }
              },
              {
                "data": "tags",
                "render": function (data, type, row, meta) {
                  if (type === 'display' && data != null) {
                    let datas = '';
                    for (let index in data) {
                        datas +=  get_tag_from_data(data[index]['tag_title'], 'badge badge-primary');
                    }
                    return datas;
                  } else if (type === 'sort' || type === 'filter') {
                      let datas = '';
                      for (let index in data) {
                         datas += ' '+ data[index]['tag_title'];
                      }
                      return datas;
                  }
                  return data;
                }
              }
        ],
        filter: true,
        info: true,
        ordering: true,
        processing: true,
        retrieve: true,
        lengthChange: false,
        pageLength: 10,
        order: [[ 2, "asc" ]],
        buttons: [
            { "extend": 'csvHtml5', "text":'Export',"className": 'btn btn-primary btn-border btn-round btn-sm float-left mr-4 mt-2' },
            { "extend": 'copyHtml5', "text":'Copy',"className": 'btn btn-primary btn-border btn-round btn-sm float-left mr-4 mt-2' },
        ],
        select: true
    });

    $("#ucases_table").css("font-size", 12);

    UserTaskTable = $("#utasks_table").DataTable({
        dom: 'Blfrtip',
        aaData: [],
        aoColumns: [
          {
            "data": "task_title",
            "render": function (data, type, row, meta) {
              if (type === 'display') {
                  let a_anchor = $('<a>');
                    a_anchor.attr('href', `case/tasks?cid=${row['case_id']}&shared=${row['task_id']}`);
                    a_anchor.attr('target', '_blank');
                    a_anchor.attr('rel', 'noopener');
                    a_anchor.title="Go to task";

                if (isWhiteSpace(data)) {
                    data = '#' + row['task_id'];
                }

                a_anchor.text(data);
                return a_anchor[0].outerHTML;
              }
              return data;
            }
          },
          { "data": "task_description",
           "render": function (data, type, row, meta) {
              if (type === 'display') {
                  return ret_obj_dt_description(data);
              }
              return data;
            }
          },
          {
            "data": "task_status_id",
            "render": function(data, type, row, meta) {
               if (type === 'display') {
                  data = sanitizeHTML(data);
                  data = '<span class="badge ml-2 badge-'+ row['status_bscolor'] +'">' + row['status_name'] + '</span>';
              }
              return data;
            }
          },
          {
            "data": "task_case",
            "render": function (data, type, row, meta) {
                if (type === 'display') {
                    let a_anchor = $('<a>');
                    a_anchor.attr('href', '/case?cid='+ row['case_id']);
                    a_anchor.text(data);
                    a_anchor.title="Go to case";
                    return a_anchor[0].outerHTML;
                }
                return data;
              }
          },
          {
            "data": "task_last_update",
            "render": function (data, type, row, meta) {
              if (type === 'display' && data != null) {
                  return render_date(data);
              }
              return data;
            }
          },
          { "data": "task_tags",
            "render": function (data, type, row, meta) {
              if (type === 'display' && data != null) {
                  let tags = "";
                  let de = data.split(',');
                  for (let tag in de) {
                      tags +=  get_tag_from_data(de[tag], 'badge badge-primary');
                  }
                  return tags;
              }
              return data;
            }
          }
        ],
        rowCallback: function (nRow, data) {
            data = sanitizeHTML(data);
            nRow = '<span class="badge ml-2 badge-'+ sanitizeHTML(data['status_bscolor']) +'">' + sanitizeHTML(data['status_name']) + '</span>';
        },
        filter: true,
        info: true,
        ordering: true,
        processing: true,
        retrieve: true,
        lengthChange: false,
        pageLength: 10,
        order: [[ 2, "asc" ]],
        buttons: [
            { "extend": 'csvHtml5', "text":'Export',"className": 'btn btn-primary btn-border btn-round btn-sm float-left mr-4 mt-2' },
            { "extend": 'copyHtml5', "text":'Copy',"className": 'btn btn-primary btn-border btn-round btn-sm float-left mr-4 mt-2' },
        ],
        select: true
    });
    $("#utasks_table").css("font-size", 12);

    Table = $("#gtasks_table").DataTable({
        dom: 'Blfrtip',
        aaData: [],
        aoColumns: [
          {
            "data": "task_title",
            "render": function (data, type, row, meta) {
              if (type === 'display') {
                  let a_anchor = $('<a>');
                  a_anchor.attr('onclick', `edit_gtask(${row['task_id']});return false;`);
                  a_anchor.attr('href', 'javascript:void(0);');
                  a_anchor.title="Edit task";

                if (isWhiteSpace(data)) {
                    data = '#' + row['task_id'];
                }

                a_anchor.text(data);
                return a_anchor[0].outerHTML;

              }
              return data;
            }
          },
          { "data": "task_description",
           "render": function (data, type, row, meta) {
              if (type === 'display') {
                return ret_obj_dt_description(data);
              }
              return data;
            }
          },
          {
            "data": "task_status_id",
            "render": function(data, type, row, meta) {
                if (type === 'display' && data != null) {
                    data = sanitizeHTML(data);
                    data = '<span class="badge ml-2 badge-'+ row['status_bscolor'] +'">' + row['status_name'] + '</span>';
                }
              return data;
            }
          },
          {
            "data": "user_name",
            "render": function (data, type, row, meta) {
                if (type === 'display') { data = sanitizeHTML(data);}
                return data;
              }
          },
          {
            "data": "task_last_update",
            "render": function (data, type, row, meta) {
              if (type === 'display' && data != null) {
                  return render_date(data);
              }
              return data;
            }
          },
          { "data": "task_tags",
            "render": function (data, type, row, meta) {
              if (type === 'display' && data != null) {
                  let tags = "";
                  let de = data.split(',');
                  for (let tag in de) {
                        tags += get_tag_from_data(de[tag], 'badge badge-primary ml-2');
                  }
                  return tags;
              }
              return data;
            }
          }
        ],
        rowCallback: function (nRow, data) {
            nRow = '<span class="badge ml-2 badge-'+ sanitizeHTML(data['status_bscolor']) +'">' + sanitizeHTML(data['status_name']) + '</span>';
        },
        filter: true,
        info: true,
        ordering: true,
        processing: true,
        retrieve: true,
        lengthChange: false,
        pageLength: 10,
        order: [[ 2, "asc" ]],
        buttons: [
            { "extend": 'csvHtml5', "text":'Export',"className": 'btn btn-primary btn-border btn-round btn-sm float-left mr-4 mt-2' },
            { "extend": 'copyHtml5', "text":'Copy',"className": 'btn btn-primary btn-border btn-round btn-sm float-left mr-4 mt-2' },
        ],
        select: true
    });
    $("#gtasks_table").css("font-size", 12);

    update_utasks_list();
    update_ucases_list();
    update_ureviews_list();
    update_gtasks_list();
    setInterval(check_page_update,30000);
});