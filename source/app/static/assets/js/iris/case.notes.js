let note_editor;
let session_id = null;
let collaborator = null;
let collaborator_socket = null;
let last_applied_change = null;
let just_cleared_buffer = null;
let is_typing = "";
let ppl_viewing = /* @__PURE__ */ new Map();
let timer_socket = 0;
let note_id = null;
let last_ping = 0;
let cid = null;
let previousNoteTitle = null;
let timer = null;
let timeout = 5e3;
const preventFormDefaultBehaviourOnSubmit = (event) => {
  event.preventDefault();
  return false;
};
function Collaborator(session_id2, n_id) {
  this.collaboration_socket = collaborator_socket;
  this.channel = "case-" + session_id2 + "-notes";
  this.collaboration_socket.off("change-note");
  this.collaboration_socket.off("clear_buffer-note");
  this.collaboration_socket.off("save-note");
  this.collaboration_socket.off("leave-note");
  this.collaboration_socket.off("join-note");
  this.collaboration_socket.off("pong-note");
  this.collaboration_socket.off("disconnect");
  this.collaboration_socket.on("change-note", (function(data) {
    if (parseInt(data.note_id) !== parseInt(note_id)) return;
    let delta = JSON.parse(data.delta);
    last_applied_change = delta;
    $("#content_typing").text(data.last_change + " is typing..");
    if (delta !== null && delta !== void 0) {
      note_editor.session.getDocument().applyDeltas([delta]);
    }
  }).bind(this));
  this.collaboration_socket.on("clear_buffer-note", (function(data) {
    if (parseInt(data.note_id) !== parseInt(note_id)) return;
    just_cleared_buffer = true;
    note_editor.setValue("");
  }).bind(this));
  this.collaboration_socket.on("save-note", (function(data2) {
    if (parseInt(data2.note_id) !== parseInt(note_id)) return;
    sync_note(note_id).then(function() {
      $("#content_last_saved_by").text("Last saved by " + data2.last_saved);
      $("#btn_save_note").text("Saved").addClass("btn-success").removeClass("btn-danger").removeClass("btn-warning");
      $("#last_saved").removeClass("btn-danger").addClass("btn-success");
      $("#last_saved > i").attr("class", "fa-solid fa-file-circle-check");
    });
  }).bind());
  this.collaboration_socket.on("leave-note", function(data2) {
    ppl_viewing.delete(data2.user);
    refresh_ppl_list(session_id2, note_id);
  });
  this.collaboration_socket.on("join-note", function(data2) {
    if (parseInt(data2.note_id) !== parseInt(note_id)) return;
    if (data2.user in ppl_viewing) return;
    ppl_viewing.set(filterXSS(data2.user), 1);
    refresh_ppl_list(session_id2, note_id);
    collaborator.collaboration_socket.emit("ping-note", { "channel": collaborator.channel, "note_id": note_id });
  });
  this.collaboration_socket.on("ping-note", function(data2) {
    if (data2.note_id !== note_id) return;
    collaborator.collaboration_socket.emit("pong-note", { "channel": collaborator.channel, "note_id": note_id });
  });
  this.collaboration_socket.on("disconnect", function(data2) {
    ppl_viewing.delete(data2.user);
    refresh_ppl_list(session_id2, note_id);
  });
}
Collaborator.prototype.change = function(delta, note_id2) {
  this.collaboration_socket.emit("change-note", { "delta": delta, "channel": this.channel, "note_id": note_id2 });
};
Collaborator.prototype.clear_buffer = function(note_id2) {
  this.collaboration_socket.emit("clear_buffer-note", { "channel": this.channel, "note_id": note_id2 });
};
Collaborator.prototype.save = function(note_id2) {
  this.collaboration_socket.emit("save-note", { "channel": this.channel, "note_id": note_id2 });
};
Collaborator.prototype.close = function(note_id2) {
  this.collaboration_socket.emit("leave-note", { "channel": this.channel, "note_id": note_id2 });
};
function auto_remove_typing() {
  if ($("#content_typing").text() == is_typing) {
    $("#content_typing").text("");
  } else {
    is_typing = $("#content_typing").text();
  }
}
let current_id = 0;
var current_gid = 0;
async function get_remote_note(note_id2) {
  return get_request_api(`/case/notes/${note_id2}`);
}
async function sync_note(node_id) {
  let remote_note = await get_remote_note(node_id);
  if (remote_note.status !== "success") {
    return;
  }

  let local_note = note_editor.getValue();
  if (local_note === "") {
    note_editor.setValue(remote_note.data.note_content, -1);
    return;
  }

  // Only check for conflicts if content has actually changed
  if (local_note !== remote_note.data.note_content && !just_cleared_buffer) {
    swal({
      title: "Note conflict",
      text: "The note has been saved by someone else. Do you want to overwrite your changes?",
      icon: "warning",
      buttons: {
        cancel: {
          text: "Cancel",
          value: null,
          visible: true
        },
        confirm: {
          text: "Overwrite",
          value: true
        },
        merge: {
          text: "Keep both",
          value: "merge"
        }
      },
      dangerMode: true,
      closeOnEsc: false,
      allowOutsideClick: false,
      allowEnterKey: false
    }).then((action) => {
      if (action === true) {
        note_editor.setValue(remote_note.data.note_content, -1);
      } else if (action === "merge") {
        // Append local content to remote content with a separator
        note_editor.setValue(
          remote_note.data.note_content +
          "\n\n------- MERGED CONTENT -------\n\n" +
          local_note,
          -1
        );
      }
    });
  }
  return;
}
function delete_note(_item, cid2) {
  if (_item === void 0 || _item === null) {
    _item = $("#currentNoteIDLabel").data("note_id");
  }
  do_deletion_prompt("You are about to delete note #" + _item).then((doDelete) => {
    if (doDelete) {
      post_request_api("/case/notes/delete/" + _item, null, null, cid2).done((data2) => {
        if (notify_auto_api(data2)) {
          load_directories().then(
            (data3) => {
              let shared_id = getSharedLink();
              if (shared_id) {
                note_detail(shared_id).then((data4) => {
                  if (!data4) {
                    setSharedLink(null);
                    toggleNoteEditor(false);
                  }
                });
              }
            }
          );
        }
      });
    }
  });
}
function proxy_comment_element() {
  let note_id2 = $("#currentNoteIDLabel").data("note_id");
  return comment_element(note_id2, "notes");
}
function proxy_copy_object_link() {
  let note_id2 = $("#currentNoteIDLabel").data("note_id");
  return copy_object_link(note_id2);
}
function proxy_copy_object_link_md() {
  let note_id2 = $("#currentNoteIDLabel").data("note_id");
  return copy_object_link_md("note", note_id2);
}
function toggleNoteEditor(show_editor) {
  if (show_editor) {
    $("#currentNoteContent").show();
    $("#emptyNoteDisplay").hide();
  } else {
    $("#currentNoteContent").hide();
    $("#emptyNoteDisplay").show();
  }
}
function edit_note(event) {
  var nval = $(event).find("iris_note").attr("id");
  collaborator = null;
  note_detail(nval);
}
function setSharedLink(id) {
  let url2 = new URL(window.location.href);
  if (id !== void 0 && id !== null) {
    url2.searchParams.set("shared", id);
  } else {
    url2.searchParams.delete("shared");
  }
  window.history.replaceState({}, "", url2);
}
async function load_note_revisions(_item) {
  if (_item === void 0 || _item === null) {
    _item = $("#currentNoteIDLabel").data("note_id");
  }
  get_request_api(`/case/notes/${_item}/revisions/list`).done((data2) => {
    if (api_request_failed(data2)) {
      return false;
    }
    let revisions = data2.data;
    let revisionList = $("#revisionList");
    revisionList.empty();
    revisions.forEach(function(revision) {
      let listItem = $("<li></li>").addClass("list-group-item");
      let link = $('<a class="btn btn-sm btn-outline-dark float-right ml-1" href="#"><i class="fa-solid fa-clock-rotate-left" style="cursor: pointer;" title="Revert"></i> Revert</a>');
      let link_preview = $('<a class="btn btn-sm btn-outline-dark float-right ml-1" href="#"><i class="fa-solid fa-eye" style="cursor: pointer;" title="Preview"></i> Preview</a>');
      let link_delete = $('<a class="btn btn-sm btn-outline-danger float-right ml-1" href="#"><i class="fa-solid fa-trash" style="cursor: pointer;" title="Delete"></i></a>');
      let user = $("<span></span>").text(`#${revision.revision_number} by ${revision.user_name} on ${formatTime(revision.revision_timestamp)}`);
      listItem.append(user);
      listItem.append(link_delete);
      listItem.append(link);
      listItem.append(link_preview);
      revisionList.append(listItem);
      link.on("click", function(e2) {
        e2.preventDefault();
        note_revision_revert(_item, revision.revision_number);
      });
      link_delete.on("click", function(e2) {
        e2.preventDefault();
        note_revision_delete(_item, revision.revision_number);
      });
      link_preview.on("click", function(e2) {
        e2.preventDefault();
        get_request_api(`/case/notes/${_item}/revisions/${revision.revision_number}`).done((data3) => {
          if (api_request_failed(data3)) {
            return;
          }
          let revision2 = data3.data;
          $("#previewRevisionID").text(revision2.revision_number);
          $("#notePreviewModalTitle").text(`#${revision2.revision_number} - ${revision2.note_title}`);
          let note_prev = get_new_ace_editor("notePreviewModalContent", "note_content", "targetDiv");
          note_prev.setValue(revision2.note_content, -1);
          note_prev.setReadOnly(true);
          $("#notePreviewModal").modal("show");
        });
      });
      $("#noteModificationHistoryModal").modal("show");
    });
  });
}
function note_revision_revert(_item, _rev) {
  if (_item === void 0 || _item === null) {
    _item = $("#currentNoteIDLabel").data("note_id");
  }
  let close_modal = false;
  if (_rev === void 0 || _rev === null) {
    _rev = $("#previewRevisionID").text();
    close_modal = true;
  }
  get_request_api(`/case/notes/${_item}/revisions/${_rev}`).done((data2) => {
    if (api_request_failed(data2)) {
      return;
    }
    let revision = data2.data;
    $("#currentNoteTitle").text(revision.note_title);
    note_editor.setValue(revision.note_content, -1);
    if (close_modal) {
      $("#notePreviewModal").modal("hide");
    }
    $("#noteModificationHistoryModal").modal("hide");
    notify_success("Note reverted to revision #" + _rev + ". Save to apply changes.");
  });
}
function note_revision_delete(_item, _rev) {
  if (_item === void 0 || _item === null) {
    _item = $("#currentNoteIDLabel").data("note_id");
  }
  let close_modal = false;
  if (_rev === void 0 || _rev === null) {
    _rev = $("#previewRevisionID").text();
    close_modal = true;
  }
  do_deletion_prompt("You are about to delete revision #" + _rev).then((doDelete) => {
    if (doDelete) {
      post_request_api("/case/notes/" + _item + "/revisions/" + _rev + "/delete").done((data2) => {
        if (notify_auto_api(data2)) {
          load_note_revisions(_item);
        }
        if (close_modal) {
          $("#notePreviewModal").modal("hide");
        }
      });
    }
  });
}
async function note_detail(id) {
  get_request_api(`/case/notes/${id}`).done((data2) => {
    if (data2.status === "success") {
      let timer2;
      let timeout2 = 1e4;
      $("#form_note").keyup(function() {
        if (timer2) {
          clearTimeout(timer2);
        }
        if (ppl_viewing.size <= 1) {
          timer2 = setTimeout(save_note, timeout2);
        }
      });
      note_id = id;
      if (collaborator !== null) {
        collaborator.close(note_id);
      }
      collaborator = new Collaborator(get_caseid());
      if (note_editor !== void 0 && note_editor !== null) {
        note_editor.destroy();
        note_editor = null;
      }
      note_editor = get_new_ace_editor("editor_detail", "note_content", "targetDiv", function() {
        $("#last_saved").addClass("btn-danger").removeClass("btn-success");
        $("#last_saved > i").attr("class", "fa-solid fa-file-circle-exclamation");
        $("#btn_save_note").text("Save").removeClass("btn-success").addClass("btn-warning").removeClass("btn-danger");
      }, save_note);
      note_editor.focus();
      note_editor.setValue(data2.data.note_content, -1);
      $("#currentNoteTitle").text(data2.data.note_title);
      previousNoteTitle = data2.data.note_title;
      $("#currentNoteIDLabel").text(`#${data2.data.note_id} - ${data2.data.note_uuid}`).data("note_id", data2.data.note_id);
      note_editor.on(
        "change",
        function(e2) {
          if (last_applied_change != e2 && note_editor.curOp && note_editor.curOp.command.name) {
            console.log("Change detected - signaling teammates");
            collaborator.change(JSON.stringify(e2), note_id);
          }
        },
        false
      );
      last_applied_change = null;
      just_cleared_buffer = false;
      load_menu_mod_options_modal(id, "note", $("#note_quick_actions"));
      collaborator_socket.emit("ping-note", { "channel": "case-" + get_caseid() + "-notes", "note_id": note_id });
      toggleNoteEditor(true);
      $(".note").removeClass("note-highlight");
      $("#note-" + id).addClass("note-highlight");
      $("#object_comments_number").text(data2.data.comments.length > 0 ? data2.data.comments.length : "");
      $("#content_last_saved_by").text("");
      $("#content_typing").text("");
      $("#last_saved").removeClass("btn-danger").addClass("btn-success");
      $("#last_saved > i").attr("class", "fa-solid fa-file-circle-check");
      let ed_details = $("#editor_detail");
      ed_details.keyup(function() {
        if (timer2) {
          clearTimeout(timer2);
        }
        timer2 = setTimeout(save_note, timeout2);
      });
      ed_details.off("paste");
      ed_details.on("paste", (event) => {
        event.preventDefault();
        handle_ed_paste(event);
      });
      setSharedLink(id);
      return true;
    } else {
      setSharedLink();
      return false;
    }
  });
}
function refresh_ppl_list(session_id, note_id) {
  $("#ppl_list_viewing").empty();
  for (let [key, value] of ppl_viewing) {
    $("#ppl_list_viewing").append(get_avatar_initials(key, false, void 0, true));
  }
}
function search_notes() {
  var data2 = Object();
  data2["search_term"] = $("#search_note_input").val();
  data2["csrf_token"] = $("#csrf_token").val();
  post_request_api("/case/notes/search", JSON.stringify(data2)).done((data3) => {
    if (data3.status == "success") {
      $("#notes_search_list").empty();
      for (e in data3.data) {
        let lit_tag = $("<li>");
        lit_tag.addClass("list-group-item list-group-item-action note");
        lit_tag.attr("id", "note-" + data3.data[e]["note_id"]);
        lit_tag.attr("onclick", "note_detail(" + data3.data[e]["note_id"] + ");");
        lit_tag.text(data3.data[e]["note_title"]);
        $("#notes_search_list").append(lit_tag);
      }
      $("#notes_search_list").show();
    } else {
      if (data3.message != "No data to load for dashboard") {
        swal("Oh no !", data3.message, "error");
      }
    }
  });
}
function toggle_max_editor() {
  $("#ctrd_notesum").toggle();
  if ($("#ctrd_notesum").is(":visible")) {
    $("#btn_max_editor").html('<i class="fa-solid fa-maximize"></i>');
    $("#container_note_content").removeClass("col-md-12 col-lg-12").addClass("col-md-12 col-lg-6");
  } else {
    $("#btn_max_editor").html('<i class="fa-solid fa-minimize"></i>');
    $("#container_note_content").removeClass("col-md-12 col-lg-6").addClass("col-md-12 col-lg-12");
  }
}
function save_note() {
  clear_api_error();
  let n_id = $("#currentNoteIDLabel").data("note_id");
  let data_sent = Object();
  let currentNoteTitle = $("#currentNoteTitle").text() ? $("#currentNoteTitle").text() : $("#currentNoteTitleInput").val();
  data_sent["note_title"] = currentNoteTitle;
  data_sent["csrf_token"] = $("#csrf_token").val();
  data_sent["note_content"] = $("#note_content").val();
  let ret = get_custom_attributes_fields();
  let has_error = ret[0].length > 0;
  let attributes = ret[1];
  if (has_error) {
    return false;
  }
  data_sent["custom_attributes"] = attributes;
  post_request_api("/case/notes/update/" + n_id, JSON.stringify(data_sent), false, void 0, cid, function() {
    $("#btn_save_note").text("Error saving!").removeClass("btn-success").addClass("btn-danger").removeClass("btn-danger");
    $("#last_saved > i").attr("class", "fa-solid fa-file-circle-xmark");
    $("#last_saved").addClass("btn-danger").removeClass("btn-success");
  }).done((data2) => {
    if (api_request_failed(data2)) {
      return;
    }
    $("#btn_save_note").text("Saved").addClass("btn-success").removeClass("btn-danger").removeClass("btn-warning");
    $("#last_saved").removeClass("btn-danger").addClass("btn-success");
    $("#content_last_saved_by").text("Last saved by you");
    $("#last_saved > i").attr("class", "fa-solid fa-file-circle-check");
    collaborator.save(n_id);
    if (previousNoteTitle !== currentNoteTitle) {
      load_directories().then(function() {
        $(".note").removeClass("note-highlight");
        $("#note-" + n_id).addClass("note-highlight");
      });
      previousNoteTitle = currentNoteTitle;
    }
  });
}
function edit_innote() {
  $("#container_note_content").toggle();
  if ($("#container_note_content").is(":visible")) {
    $("#notes_edition_btn").show(100);
    $("#ctrd_notesum").removeClass("col-md-11 col-lg-11 ml-4").addClass("col-md-6 col-lg-6");
  } else {
    $("#notes_edition_btn").hide(100);
    $("#ctrd_notesum").removeClass("col-md-6 col-lg-6").addClass("col-md-11 col-lg-11 ml-4");
  }
}
async function load_directories() {
  return get_request_api("/case/notes/directories/filter").done((data2) => {
    if (api_request_failed(data2)) {
      return;
    }
    data2 = data2.data;
    let directoriesListing = $("#directoriesListing");
    directoriesListing.empty();
    let directoryMap = /* @__PURE__ */ new Map();
    data2.forEach(function(directory) {
      directoryMap.set(directory.id, directory);
    });
    let subdirectoryIds = /* @__PURE__ */ new Set();
    data2.forEach(function(directory) {
      directory.subdirectories.forEach(function(subdirectory) {
        subdirectoryIds.add(subdirectory.id);
      });
    });
    let directories = data2.filter(function(directory) {
      return !subdirectoryIds.has(directory.id);
    });
    directories.forEach(function(directory) {
      directoriesListing.append(createDirectoryListItem(directory, directoryMap));
    });
  });
}
function download_note() {
  let content = note_editor.getValue();
  let filename = $("#currentNoteTitle").text() + ".md";
  let blob = new Blob([content], { type: "text/plain" });
  let url2 = window.URL.createObjectURL(blob);
  let link = document.createElement("a");
  link.href = url2;
  link.download = filename;
  link.click();
}
function add_note(directory_id) {
  let data2 = Object();
  data2["directory_id"] = directory_id;
  data2["note_title"] = "New note";
  data2["note_content"] = "";
  data2["csrf_token"] = $("#csrf_token").val();
  post_request_api("/case/notes/add", JSON.stringify(data2)).done((data3) => {
    if (api_request_failed(data3)) {
      return;
    }
    note_detail(data3.data.note_id);
    load_directories().then(function() {
      $(".note").removeClass("note-highlight");
      $("#note-" + data3.data.note_id).addClass("note-highlight");
    });
  });
}
function add_folder(directory_id) {
  let data2 = Object();
  data2["parent_id"] = directory_id;
  data2["name"] = "New folder";
  data2["csrf_token"] = $("#csrf_token").val();
  post_request_api("/case/notes/directories/add", JSON.stringify(data2)).done((data3) => {
    if (api_request_failed(data3)) {
      return;
    }
    rename_folder(data3.data.id);
  });
}
function refresh_folders() {
  load_directories().then(function() {
    notify_success("Tree  refreshed");
    let note_id2 = $("#currentNoteIDLabel").data("note_id");
    $(".note").removeClass("note-highlight");
    $("#note-" + note_id2).addClass("note-highlight");
  });
}
function toggleDirectories() {
  let directories = $(".directory-container");
  directories.toggle();
}
function rename_folder_api(directory_id, newName) {
  let data2 = Object();
  data2["name"] = newName;
  data2["csrf_token"] = $("#csrf_token").val();
  post_request_api(
    `/case/notes/directories/update/${directory_id}`,
    JSON.stringify(data2)
  ).done((data3) => {
    if (notify_auto_api(data3)) {
      load_directories();
    }
  });
}
function delete_folder_api(directory_id) {
  let data2 = Object();
  data2["csrf_token"] = $("#csrf_token").val();
  post_request_api(
    `/case/notes/directories/delete/${directory_id}`,
    JSON.stringify(data2)
  ).done((data3) => {
    if (notify_auto_api(data3)) {
      load_directories();
    }
  });
}
function move_note_api(note_id2, new_directory_id) {
  let data2 = Object();
  data2["csrf_token"] = $("#csrf_token").val();
  data2["directory_id"] = new_directory_id;
  return post_request_api(
    `/case/notes/update/${note_id2}`,
    JSON.stringify(data2)
  );
}
function move_item(item_id, item_type) {
  let modal = $("#moveFolderModal");
  let directoriesListing = $("<ul></ul>");
  $("#dirListingMove").empty().append(directoriesListing);
  let directoryMap = /* @__PURE__ */ new Map();
  $("#directoriesListing").find("li").filter(".directory").each(function() {
    let directory = $(this).data("directory");
    directoryMap.set(directory.id, directory);
  });
  let subdirectoryIds = /* @__PURE__ */ new Set();
  function addSubdirectoryIds(directory) {
    directory.subdirectories.forEach(function(subdirectory) {
      subdirectoryIds.add(subdirectory.id);
      let subdirectoryData = directoryMap.get(subdirectory.id);
      if (subdirectoryData) {
        addSubdirectoryIds(subdirectoryData);
      }
    });
  }
  directoryMap.forEach(function(directory) {
    addSubdirectoryIds(directory);
  });
  let directories = Array.from(directoryMap.values()).filter(function(directory) {
    return item_type === "folder" ? item_id !== directory.id : true;
  });
  let listItem = $("<li></li>");
  let link = $("<a></a>").attr("href", "#").text("Root");
  listItem.append(link);
  link.on("click", function(e2) {
    e2.preventDefault();
    if (item_type === "note") {
      move_note_api(item_id, null).then(function() {
        modal.modal("hide");
      });
    } else if (item_type === "folder") {
      move_folder_api(item_id, null).then(function() {
        modal.modal("hide");
      });
    }
  });
  directoriesListing.append(listItem);
  directories.forEach(function(directory) {
    let listItem2 = $("<li></li>");
    let link2 = $("<a></a>").attr("href", "#");
    link2.append($("<i></i>").addClass("fa-regular fa-folder mr-2"));
    link2.append(" " + directory.name);
    listItem2.append(link2);
    link2.on("click", function(e2) {
      e2.preventDefault();
      if (item_type === "note") {
        move_note_api(item_id, directory.id).then(function() {
          load_directories().then(function() {
            note_detail(item_id);
            modal.modal("hide");
          });
        });
      } else if (item_type === "folder") {
        move_folder_api(item_id, directory.id).then(function() {
          load_directories().then(function() {
            modal.modal("hide");
          });
        });
      }
    });
    directoriesListing.append(listItem2);
  });
  modal.modal("show");
}
async function move_folder_api(directory_id, new_parent_id) {
  let data2 = Object();
  data2["csrf_token"] = $("#csrf_token").val();
  data2["parent_id"] = new_parent_id;
  return post_request_api(
    `/case/notes/directories/update/${directory_id}`,
    JSON.stringify(data2)
  ).done((data3) => {
    if (notify_auto_api(data3)) {
      load_directories();
    }
  });
}
function delete_folder(directory_id) {
  swal({
    title: "Delete folder",
    text: "Are you sure you want to delete this folder? All subfolders and notes will be deleted as well.",
    icon: "warning",
    buttons: {
      cancel: {
        text: "Cancel",
        value: null,
        visible: true
      },
      confirm: {
        text: "Delete",
        value: true
      }
    },
    dangerMode: true,
    closeOnEsc: false,
    allowOutsideClick: false,
    allowEnterKey: false
  }).then((willDelete) => {
    if (willDelete) {
      delete_folder_api(directory_id);
    }
  });
}
function rename_folder(directory_id, new_directory = false) {
  swal({
    title: new_directory ? "Rename directory" : "Name the new folder",
    text: "Enter a new name for the folder",
    content: "input",
    buttons: {
      cancel: {
        text: "Cancel",
        value: null,
        visible: true
      },
      confirm: {
        text: new_directory ? "Ok" : "Rename",
        value: true
      }
    },
    dangerMode: true,
    closeOnEsc: false,
    allowOutsideClick: false,
    allowEnterKey: false
  }).then((newName) => {
    if (newName) {
      rename_folder_api(directory_id, newName);
    }
  });
}
function fetchNotes(searchInput) {
  get_raw_request_api(`/case/notes/search?search_input=${encodeURIComponent(searchInput)}&cid=${get_caseid()}`).done((data2) => {
    if (api_request_failed(data2)) {
      return;
    }
    $(".directory-container").find("li").hide();
    $(".directory").hide();
    $(".note").hide();
    data2.data.forEach((note) => {
      $("#note-" + note.note_id).show();
      let parentDirectory = $("#directory-" + note.directory_id);
      while (parentDirectory.length > 0) {
        parentDirectory.show();
        parentDirectory = parentDirectory.parents(".directory").first();
      }
    });
  });
}
function getNotesInfo(directory, directoryMap, currentNoteID) {
  let totalNotes = directory.notes.length;
  let hasMoreThanFiveNotes = directory.notes.length > 5;
  let dirContainsCurrentNote = directory.notes.some((note) => note.id == currentNoteID);
  for (let i = 0; i < directory.subdirectories.length; i++) {
    let subdirectoryId = directory.subdirectories[i].id;
    let subdirectory = directoryMap.get(subdirectoryId);
    if (subdirectory) {
      let subdirectoryInfo = getNotesInfo(subdirectory, directoryMap, currentNoteID);
      totalNotes += subdirectoryInfo.totalNotes;
      hasMoreThanFiveNotes = hasMoreThanFiveNotes || subdirectoryInfo.hasMoreThanFiveNotes;
      dirContainsCurrentNote = dirContainsCurrentNote || subdirectoryInfo.dirContainsCurrentNote;
    }
  }
  return { totalNotes, hasMoreThanFiveNotes, dirContainsCurrentNote };
}
function createDirectoryListItem(directory, directoryMap) {
  var listItem = $("<li></li>").attr("id", "directory-" + directory.id).addClass("directory");
  listItem.data("directory", directory);
  var link = $("<a></a>").attr("href", "#");
  var icon = $("<i></i>").addClass("fa-regular fa-folder");
  link.append(icon);
  link.append($("<span>").text(directory.name));
  listItem.append(link);
  let currentNoteID = getSharedLink();
  var container = $("<div></div>").addClass("directory-container");
  listItem.append(container);
  let notesInfo = getNotesInfo(directory, directoryMap, currentNoteID);
  icon.append($("<span></span>").addClass("notes-number").text(notesInfo.totalNotes));
  if (!notesInfo.hasMoreThanFiveNotes || notesInfo.dirContainsCurrentNote) {
    icon.removeClass("fa-folder").addClass("fa-folder-open");
  } else {
    container.hide();
  }
  link.on("click", function(e2) {
    e2.preventDefault();
    container.slideToggle();
    icon.toggleClass("fa-folder fa-folder-open");
  });
  link.on("contextmenu", function(e2) {
    e2.preventDefault();
    let menu = $("<div></div>").addClass("dropdown-menu show").css({
      position: "absolute",
      left: e2.pageX,
      top: e2.pageY
    });
    menu.append($("<a></a>").addClass("dropdown-item").attr("href", "#").text("Add note").on("click", function(e3) {
      e3.preventDefault();
      add_note(directory.id);
    }));
    menu.append($("<a></a>").addClass("dropdown-item").attr("href", "#").text("Add directory").on("click", function(e3) {
      e3.preventDefault();
      add_folder(directory.id);
    }));
    menu.append($("<div></div>").addClass("dropdown-divider"));
    menu.append($("<a></a>").addClass("dropdown-item").attr("href", "#").text("Rename").on("click", function(e3) {
      e3.preventDefault();
      rename_folder(directory.id);
    }));
    menu.append($("<a></a>").addClass("dropdown-item").attr("href", "#").text("Move").on("click", function(e3) {
      e3.preventDefault();
      move_item(directory.id, "folder");
    }));
    menu.append($("<div></div>").addClass("dropdown-divider"));
    menu.append($("<a></a>").addClass("dropdown-item text-danger").attr("href", "#").text("Delete").on("click", function(e3) {
      e3.preventDefault();
      delete_folder(directory.id);
    }));
    $("body").append(menu).on("click", function() {
      menu.remove();
    });
  });
  if (directory.subdirectories && directory.subdirectories.length > 0) {
    var subdirectoriesList = $("<ul></ul>").addClass("nav");
    directory.subdirectories.forEach(function(subdirectory) {
      var subdirectoryData = directoryMap.get(subdirectory.id);
      if (subdirectoryData) {
        subdirectoriesList.append(createDirectoryListItem(subdirectoryData, directoryMap));
      }
    });
    container.append(subdirectoriesList);
  }
  if (directory.notes && directory.notes.length > 0) {
    var notesList = $("<ul></ul>").addClass("nav");
    directory.notes.forEach(function(note) {
      var noteListItem = $("<li></li>").attr("id", "note-" + note.id).addClass("note");
      var noteLink = $("<a></a>").attr("href", "#");
      noteLink.append($("<i></i>").addClass("fa-regular fa-file"));
      noteLink.append($("<span>").text(note.title));
      noteLink.on("click", function(e2) {
        e2.preventDefault();
        note_detail(note.id);
        $(".note").removeClass("note-highlight");
        noteListItem.addClass("note-highlight");
      });
      noteLink.on("contextmenu", function(e2) {
        e2.preventDefault();
        let menu = $("<div></div>").addClass("dropdown-menu show").css({
          position: "absolute",
          left: e2.pageX,
          top: e2.pageY
        });
        menu.append($("<a></a>").addClass("dropdown-item").attr("href", "#").text("Copy link").on("click", function(e3) {
          e3.preventDefault();
          copy_object_link(note.id);
        }));
        menu.append($("<a></a>").addClass("dropdown-item").attr("href", "#").text("Copy MD link").on("click", function(e3) {
          e3.preventDefault();
          copy_object_link_md("notes", note.id);
        }));
        menu.append($("<a></a>").addClass("dropdown-item").attr("href", "#").text("Move").on("click", function(e3) {
          e3.preventDefault();
          move_item(note.id, "note");
        }));
        menu.append($("<div></div>").addClass("dropdown-divider"));
        menu.append($("<a></a>").addClass("dropdown-item text-danger").attr("href", "#").text("Delete").on("click", function(e3) {
          e3.preventDefault();
          delete_note(note.id, cid);
        }));
        $("body").append(menu).on("click", function() {
          menu.remove();
        });
      });
      noteListItem.append(noteLink);
      notesList.append(noteListItem);
    });
    container.append(notesList);
  }
  return listItem;
}
function handle_ed_paste(event) {
  let filename = null;
  const { items } = event.originalEvent.clipboardData;
  for (let i = 0; i < items.length; i += 1) {
    const item = items[i];
    if (item.kind === "string") {
      item.getAsString(function(s) {
        filename = $.trim(s.replace(/\t|\n|\r/g, "")).substring(0, 40);
      });
    }
    if (item.kind === "file") {
      const blob = item.getAsFile();
      if (blob !== null) {
        const reader = new FileReader();
        reader.onload = (e2) => {
          notify_success("The file is uploading in background. Don't leave the page");
          if (filename === null) {
            filename = random_filename(25);
          }
          upload_interactive_data(e2.target.result, filename, function(data2) {
            url = data2.data.file_url + case_param();
            event.preventDefault();
            note_editor.insertSnippet(`
![${filename}](${url} =100%x40%)
`);
          });
        };
        reader.readAsDataURL(blob);
      } else {
        notify_error("Unsupported direct paste of this item. Use datastore to upload.");
      }
    }
  }
}
function note_interval_pinger() {
  if (/* @__PURE__ */ new Date() - last_ping > 2e3) {
    collaborator_socket.emit(
      "ping-note",
      { "channel": "case-" + get_caseid() + "-notes", "note_id": note_id }
    );
    last_ping = /* @__PURE__ */ new Date();
  }
}
$(document).ready(function() {
  load_directories().then(
    function() {
      let shared_id = getSharedLink();
      if (shared_id) {
        note_detail(shared_id);
      }
      $(".page-aside").resizable({
        handles: "e"
      });
    }
  );
  cid = get_caseid();
  collaborator_socket = io.connect();
  collaborator_socket.emit("join-notes-overview", { "channel": "case-" + cid + "-notes" });
  collaborator_socket.on("ping-note", function(data2) {
    last_ping = /* @__PURE__ */ new Date();
    if (parseInt(data2.note_id) !== parseInt(note_id)) return;
    ppl_viewing.set(data2.user, 1);
    for (let [key, value] of ppl_viewing) {
      if (key !== data2.user) {
        ppl_viewing.set(key, value - 1);
      }
      if (value < 0) {
        ppl_viewing.delete(key);
      }
    }
    refresh_ppl_list(session_id, note_id);
  });
  timer_socket = setInterval(function() {
    note_interval_pinger();
  }, 2e3);
  collaborator_socket.emit("ping-note", { "channel": "case-" + cid + "-notes", "note_id": note_id });
  setInterval(auto_remove_typing, 1500);
  $(document).on("click", "#currentNoteTitle", function() {
    let title = $(this).text();
    let input = $("<input>");
    input.attr("id", "currentNoteTitleInput");
    input.attr("type", "text");
    input.val(title);
    input.addClass("form-control");
    $(this).replaceWith(input);
    $("#currentNoteTitleInput").focus();
  });
  $(document).on("blur", "#currentNoteTitleInput", function(e2) {
    let title = $(this).val();
    let h4 = $("<h4>");
    h4.attr("id", "currentNoteTitle");
    h4.addClass("page-title mb-0");
    h4.text(title);
    $(this).replaceWith(h4);
    save_note();
  });
  $("#search-input").keyup(function() {
    let searchInput = $(this).val();
    fetchNotes(searchInput);
  });
  $("#clear-search").on("click", function() {
    $("#search-input").val("");
    $(".directory-container").find("li").show();
    $(".directory").show();
    $(".note").show();
  });
  let typingTimer;
  note_editor.on(
    "change",
    function(e) {
      if (last_applied_change != e && note_editor.curOp && note_editor.curOp.command.name) {
        // Clear previous timer
        clearTimeout(typingTimer);

        // Send typing notification immediately
        collaborator.change(JSON.stringify(e), note_id);

        // Set a timer to clear typing indicator if no changes for 1.5 seconds
        typingTimer = setTimeout(() => {
          $("#content_typing").text("");
        }, 1500);
      }
    },
    false
  );
});
