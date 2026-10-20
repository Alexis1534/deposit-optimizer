// Клиентская часть интерфейса: проверка ввода по мере заполнения, разделение разрядов,
// кнопки «−»/«+», работа со списком банков, сортировка и отбор справочника.
(function () {
  // --- кнопки исключения банков
  document.querySelectorAll("[data-banks]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var checked = btn.dataset.banks === "all";
      document.querySelectorAll('input[name="exclude"]').forEach(function (cb) { cb.checked = checked; });
    });
  });

  // --- поле предельной суммы в один банк доступно только при отмеченном признаке (п. 4.6.2.5)
  var flag = document.getElementById("personal_enabled");
  var limit = document.getElementById("personal_limit");
  if (flag && limit) {
    flag.addEventListener("change", function () {
      limit.disabled = !flag.checked;
      if (flag.checked) limit.focus();
    });
  }


  // --- проверка вводимых значений в реальном времени (дублирует серверную проверку,
  //     которая остаётся окончательной: при отключённом JavaScript всё проверит сервер)
  var form = document.querySelector("form.params");
  if (form) {
    var AMOUNT_MIN = 1000, AMOUNT_MAX = 100000000, TERM_MIN = 1, TERM_MAX = 18,
        PERSONAL_MIN = 1000, OTHER_MAX = 1000000000;
    var touched = {};

    function clean(v) { return v.replace(/[\s\u00a0]/g, ""); }
    function intValue(raw) { var s = clean(raw); return /^\d+$/.test(s) ? parseInt(s, 10) : null; }
    function fmt(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " "); }
    function val(name) { return form.elements[name].value.trim(); }

    function checkInt(raw, lo, hi) {
      if (raw === "") return "поле не заполнено";
      var n = intValue(raw);
      if (n === null) return "требуется целое неотрицательное число без дробной части";
      if (lo !== null && n < lo) return "значение меньше допустимого (" + fmt(lo) + ")";
      if (hi !== null && n > hi) return "значение превышает допустимое (" + fmt(hi) + ")";
      return "";
    }

    function remainingBanks() {
      var boxes = form.querySelectorAll('input[name="exclude"]'), n = 0;
      for (var i = 0; i < boxes.length; i++) if (!boxes[i].checked) n++;
      return n;
    }

    // правила по полям: возвращают [ошибка, подсказка]
    var rules = {
      amount: function () { return [checkInt(val("amount"), AMOUNT_MIN, AMOUNT_MAX), ""]; },
      term_from: function () {
        var e = checkInt(val("term_from"), TERM_MIN, TERM_MAX);
        if (!e && !checkInt(val("term_to"), TERM_MIN, TERM_MAX) &&
            intValue(val("term_from")) > intValue(val("term_to"))) e = "нижняя граница срока больше верхней";
        return [e, ""];
      },
      term_to: function () { return [checkInt(val("term_to"), TERM_MIN, TERM_MAX), ""]; },
      banks: function () {
        return [remainingBanks() === 0 ? "исключены все банки — не осталось ни одного банка для рассмотрения" : "", ""];
      },
      max_banks: function () {
        var raw = val("max_banks"), left = remainingBanks();
        if (raw === "") return ["", left ? "будут рассмотрены все неисключённые банки (" + left + ")" : ""];
        var e = checkInt(raw, 1, null);
        if (!e && left && intValue(raw) > left) return ["", "больше числа неисключённых банков — будет принято " + left];
        return [e, ""];
      },
      personal_limit: function () {
        if (!document.getElementById("personal_enabled").checked) return ["", ""];
        return [checkInt(val("personal_limit"), PERSONAL_MIN, null), ""];
      },
      other_income: function () {
        var raw = val("other_income");
        if (raw === "") return ["", ""];
        var s = clean(raw);
        if (!/^\d+(?:[.,]\d{1,2})?$/.test(s))
          return ["требуется неотрицательная сумма в рублях, копейки — не более двух знаков после запятой (например, 1 500,50)", ""];
        if (parseFloat(s.replace(",", ".")) > OTHER_MAX) return ["значение превышает допустимое (1 000 000 000)", ""];
        return ["", ""];
      }
    };

    function show(name, error, hint) {
      var box = document.getElementById("f-" + name);
      if (!box) return;
      var em = box.querySelector("em"), small = box.querySelector("small.hint");
      if (error) {
        if (!em) { em = document.createElement("em"); box.appendChild(em); }
        em.textContent = error;
      } else if (em) { em.remove(); }
      box.classList.toggle("has-error", !!error);
      var input = box.querySelector("input:not([type=checkbox])");
      if (input) input.setAttribute("aria-invalid", error ? "true" : "false");
      if (hint) {
        if (!small) { small = document.createElement("small"); small.className = "hint"; box.appendChild(small); }
        small.textContent = hint;
      } else if (small) { small.remove(); }
    }

    function run(name, force) {
      var r = rules[name]();
      show(name, (force || touched[name]) ? r[0] : "", r[1]);
      return !r[0];
    }

    function runAll(force) {
      var ok = true;
      for (var name in rules) if (!run(name, force)) ok = false;
      return ok;
    }

    // какие правила зависят от каких полей
    var deps = { term_from: ["term_from", "term_to"], term_to: ["term_from", "term_to"],
                 exclude: ["banks", "max_banks"], personal_enabled: ["personal_limit"] };


    // --- разделение разрядов в полях денежных сумм (п. 4.6.2.13); пробелы между разрядами
    //     сервер при проверке убирает
    function groupInteger(digits) {
      return digits.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    }

    function formatted(raw, kind) {
      var cleaned = raw.replace(/[^\d.,]/g, "");
      if (kind === "int") return groupInteger(cleaned.replace(/[.,]/g, ""));
      var m = cleaned.match(/^(\d*)([.,]?)(\d{0,2})/);   // рубли, разделитель, копейки
      if (!m) return "";
      return groupInteger(m[1]) + m[2] + m[3];
    }

    function regroup(input) {
      var kind = input.dataset.group;
      if (!kind) return;
      var caret = input.selectionStart, significant = 0, i;
      for (i = 0; i < caret; i++) if (/[\d.,]/.test(input.value[i])) significant++;
      var text = formatted(input.value, kind);
      if (text === input.value) return;
      input.value = text;
      var seen = 0;
      for (i = 0; i < text.length && seen < significant; i++) if (/[\d.,]/.test(text[i])) seen++;
      try { input.setSelectionRange(i, i); } catch (e) { /* поле может не поддерживать */ }
    }

    form.querySelectorAll("input[data-group]").forEach(function (input) {
      input.value = formatted(input.value, input.dataset.group);
      input.addEventListener("input", function () { regroup(input); });
    });

    // --- кнопки «−» и «+» у срока размещения и предельного числа банков
    form.querySelectorAll(".stepper button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var input = form.elements[btn.dataset.for];
        var step = parseInt(btn.dataset.step, 10);
        var min = input.dataset.min ? parseInt(input.dataset.min, 10) : 1;
        var max = input.dataset.max ? parseInt(input.dataset.max, 10)
                                    : (input.name === "max_banks" ? remainingBanks() : Infinity);
        var current = intValue(input.value);
        // пустое поле: для числа банков это «все неисключённые», для срока — нижняя граница
        if (current === null) current = (input.name === "max_banks" ? remainingBanks() : min);
        var next = Math.min(Math.max(current + step, min), Math.max(max, min));
        input.value = String(next);
        touched[input.name] = true;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
      });
    });

    form.addEventListener("input", onChange);
    form.addEventListener("change", onChange);
    function onChange(ev) {
      var name = ev.target.name;
      if (!name) return;
      var list = deps[name] || [name];
      list.forEach(function (n) { if (rules[n]) { touched[n] = true; run(n, false); } });
    }
    document.querySelectorAll("[data-banks]").forEach(function (btn) {
      btn.addEventListener("click", function () { touched.banks = true; run("banks"); run("max_banks"); });
    });

    form.addEventListener("submit", function (ev) {
      if (!runAll(true)) {
        ev.preventDefault();
        var first = form.querySelector(".has-error input:not([type=checkbox]):not([disabled])") ||
                    form.querySelector(".has-error input");
        if (first) first.focus();
      }
    });

    // серверные ошибки остаются видимыми; подсказки показываются сразу
    run("max_banks");
    for (var n in rules) {
      var box = document.getElementById("f-" + n);
      if (box && box.classList.contains("has-error")) touched[n] = true;
    }
  }

  // --- сортировка справочника по любому столбцу
  var table = document.getElementById("catalog");
  if (!table) return;
  var tbody = table.tBodies[0];
  table.querySelectorAll("th").forEach(function (th, col) {
    th.addEventListener("click", function () {
      var asc = th.getAttribute("aria-sort") !== "ascending";
      table.querySelectorAll("th").forEach(function (h) { h.removeAttribute("aria-sort"); });
      th.setAttribute("aria-sort", asc ? "ascending" : "descending");
      var type = th.dataset.type;
      var rows = Array.prototype.slice.call(tbody.rows);
      rows.sort(function (a, b) {
        var ca = a.cells[col], cb = b.cells[col];
        var va = ca.dataset.v !== undefined ? ca.dataset.v : ca.textContent.trim();
        var vb = cb.dataset.v !== undefined ? cb.dataset.v : cb.textContent.trim();
        var r = type === "num" ? parseFloat(va) - parseFloat(vb) : va.localeCompare(vb, "ru");
        return asc ? r : -r;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
    });
  });

  // --- отбор по наименованию банка
  var filter = document.getElementById("bank-filter");
  if (filter) {
    filter.addEventListener("input", function () {
      var q = filter.value.trim().toLowerCase();
      Array.prototype.forEach.call(tbody.rows, function (r) {
        r.hidden = q !== "" && r.cells[0].textContent.toLowerCase().indexOf(q) === -1;
      });
    });
  }
})();

// --- ссылка на свёрнутый блок (например, «*» у НДФЛ): перед переходом блок раскрывается
(function () {
  function openTarget(hash) {
    if (!hash || hash.length < 2) return null;
    var target = document.getElementById(hash.slice(1));
    if (target && target.tagName === "DETAILS") target.open = true;
    return target;
  }
  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function () { openTarget(link.getAttribute("href")); });
  });
  if (location.hash) {
    var t = openTarget(location.hash);
    if (t) t.scrollIntoView();
  }
})();

// --- оглавление справки: подсветка раздела, который сейчас на экране
(function () {
  var toc = document.querySelector(".help-toc");
  if (!toc || !("IntersectionObserver" in window)) return;
  var links = {};
  toc.querySelectorAll('a[href^="#"]').forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
  var visible = {};
  function update() {
    var current = null;
    document.querySelectorAll(".help-block").forEach(function (s) {
      if (!current && visible[s.id]) current = s.id;
    });
    // в самом конце страницы подсвечивается последний раздел
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) {
      var blocks = document.querySelectorAll(".help-block");
      current = blocks[blocks.length - 1].id;
    }
    Object.keys(links).forEach(function (id) {
      if (id === current) links[id].setAttribute("aria-current", "true");
      else links[id].removeAttribute("aria-current");
    });
  }
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) { visible[e.target.id] = e.isIntersecting; });
    update();
  }, { rootMargin: "-15% 0px -55% 0px" });
  document.querySelectorAll(".help-block").forEach(function (s) { observer.observe(s); });
  window.addEventListener("scroll", update, { passive: true });
})();

// --- счётчик «В расчёте: N из M банков» над списком банков
(function () {
  var out = document.getElementById("banks-count");
  if (!out) return;
  var boxes = document.querySelectorAll('input[name="exclude"]');
  function update() {
    var left = 0;
    boxes.forEach(function (cb) { if (!cb.checked) left++; });
    var n = boxes.length, m10 = n % 10, m100 = n % 100;
    var word = (m10 === 1 && m100 !== 11) ? "банка" : "банков";   // «из 21 банка», «из 20 банков»
    out.innerHTML = "В расчёте: <b>" + left + "</b> из " + n + " " + word;
  }
  boxes.forEach(function (cb) { cb.addEventListener("change", update); });
  document.querySelectorAll("[data-banks]").forEach(function (b) { b.addEventListener("click", update); });
  update();
})();
