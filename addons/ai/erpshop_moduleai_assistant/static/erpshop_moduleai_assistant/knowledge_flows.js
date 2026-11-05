(function () {
  "use strict";

  function csrfToken() {
    var field = document.querySelector("[name=csrfmiddlewaretoken]");
    return field ? field.value : "";
  }

  function messageFrom(response) {
    if (!response || typeof response !== "object") return "The request could not be completed.";
    return response.detail || response.error_reason || "The request could not be completed.";
  }

  function request(url, options) {
    options = options || {};
    options.headers = Object.assign({ Accept: "application/json" }, options.headers || {});
    if (options.method && options.method !== "GET") options.headers["X-CSRFToken"] = csrfToken();
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () {
        return {};
      }).then(function (payload) {
        if (!response.ok) throw new Error(messageFrom(payload));
        return payload;
      });
    });
  }

  function statusText(prerequisites) {
    if (prerequisites.can_generate) {
      return "Ready: approved company-wide references and provider configuration are available.";
    }
    return (prerequisites.blockers || ["Business-flow generation is not ready."]).join(" ");
  }

  function start(root) {
    var status = root.querySelector("[data-flow-status]");
    var prerequisiteButton = root.querySelector("[data-flow-prerequisites]");
    var generateButton = root.querySelector("[data-flow-generate]");
    var reasonButton = root.querySelector("[data-flow-reason]");
    var reasonQuery = root.querySelector("[data-flow-reason-query]");
    var reasonResult = root.querySelector("[data-flow-reason-result]");
    var readPrerequisites = function () {
      return request(root.dataset.prerequisitesUrl).then(function (payload) {
        status.textContent = statusText(payload);
        generateButton.disabled = !payload.can_generate;
        return payload;
      });
    };
    var pollJob = function (jobId) {
      var url = root.dataset.jobUrlTemplate.replace("00000000-0000-0000-0000-000000000000", jobId);
      request(url).then(function (job) {
        status.textContent = "Generation " + job.status + (job.error_reason ? ": " + job.error_reason : "");
        if (job.status === "queued" || job.status === "running") {
          window.setTimeout(function () {
            pollJob(jobId);
          }, 1500);
        }
        if (job.status === "completed") {
          window.setTimeout(function () {
            window.location.reload();
          }, 700);
        }
      }).catch(function (error) {
        status.textContent = error.message;
      });
    };

    prerequisiteButton.addEventListener("click", function () {
      readPrerequisites().catch(function (error) {
        status.textContent = error.message;
      });
    });
    generateButton.addEventListener("click", function () {
      generateButton.disabled = true;
      status.textContent = "Queuing reviewed flow draft…";
      request(root.dataset.generateUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overwriteManual: false }),
      }).then(function (job) {
        status.textContent = "Generation " + job.status + ".";
        pollJob(job.job_id);
      }).catch(function (error) {
        status.textContent = error.message;
        readPrerequisites().catch(function () {});
      });
    });
    reasonButton.addEventListener("click", function () {
      var query = reasonQuery.value.trim();
      if (!query) return;
      reasonButton.disabled = true;
      reasonResult.textContent = "Searching reviewed flows…";
      request(root.dataset.reasonUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query }),
      }).then(function (result) {
        reasonResult.textContent = JSON.stringify(result, null, 2);
      }).catch(function (error) {
        reasonResult.textContent = error.message;
      }).finally(function () {
        reasonButton.disabled = false;
      });
    });
    readPrerequisites().catch(function (error) {
      status.textContent = error.message;
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-flow-tools]").forEach(start);
  });
}());
