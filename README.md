ARXIV READER FOR MUTT
=====================

This is a python script to nicely present [arxiv](http://arxiv.org/) news feed.
I use it together with the mutt email reader.  It is written using the ncurses
library :).

Configuration
-------------

Add to `~/.muttrc` file the following snippet:
```
macro index,pager X "<pipe-message>arxiv_reader.py<enter>Wo" "parse message through arxive_reader.py"
```

Then when you are over email fro arxiv type X and the scritp will parse the
email and list all the titles.

You should also set the $BROWSER environment variable, $PDFREADER.  Or just
change the BROWSER and PDFREADER variables in the script directly.  You should
also change the DOWNLOADDIR variable.  By default it is set to
``$HOME/downloads`` and if does not exist it is reset to ``/tmp``.

How to
------

Go up and down with ``j`` and ``k`` keys (all arrows).  Hit ``enter`` (or
``<space>``, or ``a``) to see the abstract.  If it was not included in the
email it will be downloaded from the arxiv web page.  If you hit ``u`` the
paper's url will be opened using your $BROWSER.  You can also save an entry to
database: with ``s``, or delete it with ``d``.  The ``g`` key will
get/download the most recent version of the paper and ``O`` will open the file
in $PDFREADER.
