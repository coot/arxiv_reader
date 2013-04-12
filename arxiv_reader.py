#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
This is a curses program (for terminal). Parse arxiv email and give a nice
interface to view: titles, abstracts and to open urls and fetch and open pdf
files. You can use this program together with the mutt email client.

Pass the email throught stdin. For example my mutt config contains a line:
macro index,pager X "<pipe-message>arxive_reader<enter>" "parse message through arxive_reader"
or even better:
macro index,pager X "Wo<up><pipe-message>arxive_reader<enter>" "parse message through arxive_reader"
(This will toggle off the new or old flag of the message.)

Use j,k or DOWN, UP arrows to go through titles. Space, Enter, or a: show
detailed description which contains: authors, abstract and comments. The same
keys will close the detailed window. If the abstract was not included in the
email it is downloaded from the article arxiv web page.

Use 'u' to open the url with BROWSER.
Use 's' to add an article to the database "${HOME}/.arxiv.db" (sqlite3),
use 'd' to remove an article from the database.

If you define ${ARXIV_AUTHORS} environment variable titles of matching authors
will be highlighted. ${ARXIV_AUTHORS} is a white space separated list of names.

It also hightlights the title if ${ARXIV_ABSTRACT_PATTERN} match the title or
the abstract.  ${ARXIV_ABSTRACT_PATTERN} is a Python pattern (can be written
like r"" litterals).

The script assumes utf8 encoding for both input (the email) and the terminal
output.
"""

"""
IDEAS:
        * TODO: check out the 'email' python module.
        * TODO: write a configuration file: for colors.
        * PORT TO __urwid__ LIBRARY: it gives 256 color support.
        * It could write a html web page with links to abstracts.
        * Use sqlite3 module, to write a database of interesting papers.
        * Write a program which can manipulate with this database.
"""

import sys
import os
import os.path
import re
import datetime
import textwrap
import curses, curses.textpad
from _curses import error as CursesError
from sgmllib import SGMLParser
import sqlite3
import subprocess
import urllib
import logging
from email.message import Message
from email.parser import Parser as email_Parser
from email.iterators import body_line_iterator as email_iterator

BROWSER = os.getenv('BROWSER')
if not BROWSER:
    BROWSER = 'chromium'
PDFREADER = os.getenv('PDFREADER')
if not PDFREADER:
    PDFREADER = 'okular'
DOWNLOADDIR = os.path.expandvars(os.path.join('$HOME', 'downloads'))
if not os.path.isdir(DOWNLOADDIR):
    DOWNLOADDIR = '/tmp'

if not hasattr(os, 'EX_OK'):
    os.EX_OK=0
if not hasattr(os, 'EX_DATAERR'):
    os.EX_DATAERR=65

__AUTHOR  = "Marcin Szamotulski"
__VERSION = "1.2"

LC_TIME = os.environ.get("LC_TIME", None)

if LC_TIME:
    import locale
    locale.setlocale(locale.LC_TIME, LC_TIME)

DB_SCHEMA="""
CREATE TABLE arxiv (
    title       text,
    authors     text,
    abstract    text,
    url         text,
    comments    text,
    categories  text,
    class       text,
    arxiv_nr    text primary key,
    time        timestamp,
    date        date,
    status      text
);
"""

"""
time    time from the arxiv
date    date when the entry was added to the database.
"""

# DONE: color titles with the given authors.
# XXX: if the window has not enough lines the program should break.
# XXX: implement help (clear window and list help)
# I should implement status line in a proper way (on the last visible line).

arxiv_db = (os.getenv("ARXIV_DB") and [os.getenv("ARXIV_DB")] or [os.path.expandvars(os.path.join("${HOME}",".arxiv.db"))])[0]
log_file = (os.getenv("ARXIV_LOG") and [os.getenv("ARXIV_LOG")] or ["/tmp/arxiv_reader.log"])[0]
logging.basicConfig(
        filename=log_file,
        format="%(funcName)s at line %(lineno)d: %(message)s",
        level=logging.INFO,
        filemode="w")
logger   = logging.getLogger("arxiv_reader")
logger.info("___ARXIV_EMAIL_PARSER__!")


class ArXivEnd(StandardError): pass
class ArXivEntryEnd(StandardError): pass

class ArXivParser(Message):
    """
    This is a simple parser of arXiv emails.

    We are subclassing email.message.Message, since this class is passed as an
    argument to email.parser.Parser (email_Parser) constructor. Then its
    parse() method returns ArXivParser instance.
    """
    def __init__(self):
        """
        sel.data    - list of dictionaries:
            { 'title'       : 'XXX',
              'authors'     : 'XXX',
              'abstract'    : 'XXX',
              'url'         : 'http://XXX',
              'comments'    : 'XXX',
              'categories'  : 'XXX',
              'class'       : 'XXX' ,
              'arxiv_nr'    : '1206.3197',
              'date'        : of the type: datetime.datetime.now() }
        """
        Message.__init__(self)
        self.message = []
            # will be filled by self.parse() method using: email_iterator(self)
        self.__len = 0
        self.data=[]
            # will be filled by self.parse() method with arxiv content data.
        self.ind = 0

    def __next_entry(self):
        logger.info("__next_entry: start pos (%d)" % self.ind)
        while self.ind < self.__len and not( self.message[self.ind] == '\\\\' and self.message[self.ind+1].startswith('arXiv:') ):
            """
            new entry starts at ind.
            """
            self.ind+=1
            if self.ind >= self.__len:
                raise ArXivEnd
        self.ind += 1
        logger.info("__next_entry: found (%d)" % self.ind)

    def parse(self):
        """Parsr the arxiv content."""

        self.message=[ l[:-1] for l in email_iterator(self) ]
        self.__len=len(self.message)
        ind = 0
        try:
            while ind <= len(self.message):
                # print("while loop: (%d)" % self.ind)
                self.__next_entry()
                data={}
                """
                Data from one entry.

                XXX: it is using try:except clause. It is better to use break.
                """
                try:
                    while self.ind < self.__len-1 and not re.match('-+$', self.message[self.ind]):
                        # print("while loop 2: (%d) %s" % (self.ind, self.message[self.ind]))
                        line = self.message[self.ind]
                        if line.startswith('arXiv:'):
                            data['arxiv_nr']=line[6:15]
                            logger.info(">> ARXIV: %s" % line[6:15])
                            self.ind+=1
                        elif line.startswith("Date: "):
                            # first remove the locale dependent info from the
                            # time (english names of the day and the month).
                            time = line[6:35].split()
                            time_str = "%s %s %s %s" % (time[1], time[3], time[4], time[5])
                            data['time'] = datetime.datetime.strptime(time_str, "%d %Y %H:%M:%S %Z")
                            logger.info(">> time %s" % data['time'])
                            self.ind+=1
                        elif line.startswith("Title: "):
                            title = line[7:]
                            self.ind+=1
                            while self.message[self.ind].startswith(' '):
                                title+=(' '+self.message[self.ind])
                                self.ind+=1
                            title = re.sub('\s+', ' ', title)
                            data['title']=title
                            logger.info(">> TITLE: %s" % title)
                        elif line.startswith("Authors: "):
                            authors = line[9:]
                            self.ind+=1
                            while self.message[self.ind].startswith(' '):
                                authors+=(' '+self.message[self.ind])
                                self.ind+=1
                            authors = re.sub('\s+', ' ', authors)
                            logger.info(">> AUTHORS: %s" % authors)
                            data['authors']=authors
                        elif line.startswith("Categories: "):
                            data["categories"] = line[12:]
                            logger.info(">> categories %s" % data['categories'])
                            self.ind+=1
                        elif line.startswith("MSC-class: "):
                            data["class"] = line[11:]
                            logger.info(">> class %s" % data['class'])
                            self.ind+=1
                        elif line.startswith("Comments: "):
                            # print("comments: START (%d): %s" % (self.ind, self.message[self.ind]))
                            comments = line[10:]
                            self.ind+=1
                            while self.message[self.ind].startswith(' '):
                                # print("comments: WHILE (%d): %s" % (self.ind, self.message[self.ind]))
                                comments+=(' '+self.message[self.ind])
                                self.ind+=1
                            comments = re.sub('\s+', ' ', comments)
                            data['comments']=comments
                            logger.info(">> comments (%d) %s" % (self.ind, data['comments']))
                            # print("comments: END (%d)" % self.ind)
                            # print("comments: %s" % comments)
                        elif line == '\\\\':
                            logger.info(">> abstract (%d)" % self.ind)
                            logger.info("__%s__" % self.message[self.ind+1])
                            self.ind += 1
                            abstract = ""
                            """ parse the abstract """
                            while not self.message[self.ind].startswith('\\\\ ( http://arxiv.org'):
                                logger.info(">>          (%d)" % self.ind)
                                abstract += (" "+self.message[self.ind])
                                self.ind += 1
                            data['abstract'] = abstract.strip()
                        elif line.startswith('\\\\ ( http://arxiv.org'):
                            comma_ind = line.index(',')
                            url = line[5:comma_ind-1]
                            data['url'] = url
                            logger.info(">> url %s" % data['url'])
                            self.ind+=1
                            raise ArXivEntryEnd
                        else:
                            logger.info(">>> skipping (%d) %s" % (self.ind, self.message[self.ind]))
                            self.ind+=1
                except ArXivEntryEnd:
                    pass

                self.data.append(data)
                self.__next_entry()

        except ArXivEnd:
            pass

        logger.info("TITLES:")
        for (i,title) in enumerate(map(lambda d: d['title'].encode("utf8"), self.data)):
            logger.info("(%d) %s" % (i,title) )

class HTML_GetVersions(SGMLParser):
    """
    This is htmlparser which reads the arxiv web page of a given paper and gets
    all its version in a list self.version_list = [ 'v1', 'v2', 'v3' ].
    """

    def reset(self):
        """ Use this instead of redefining __init__(). It is called every time
        the parser is reset and makes it possible to reuse the same parser
        object. It is called also by the __init__() method."""
        self.h2 = False
        self.b  = False
        self.in_submission_section = False
        self.version_list = []
        # super(type(self),self).reset()
        SGMLParser.reset(self)

    def start_h2(self, attrs):
        self.h2 = True

    def end_h2(self):
        self.h2 = False

    def start_b(self, attrs):
        self.b = True

    def end_b(self):
        self.b = False

    def handle_data(self, text):
        if self.h2:
            if text == "Submission history":
                self.in_submission_section = True
            else:
                self.in_submission_section = False
            return
        if self.in_submission_section:
            if self.b:
                if re.match("\[v\d+\]$", text):
                    self.version_list.append(text[1:-1])

class HTML_GetAbstract(SGMLParser):

    def reset(self):
        self._abstract=False
        self._blockquote=False
        self.abstract=""
        # super(type(self),self).reset()
        SGMLParser.reset(self)

    def start_blockquote(self, attrs):
        self._blockquote=True

    def end_blockquote(self):
        self._blockquote=False
        self._abstract=False

    def start_span(self, attr):
        self._span=True

    def end_span(self):
        self._span=False

    def handle_data(self, text):
        if self._blockquote:
            if self._span and text.startswith("Abstract"):
                self._abstract=True # is set False by self.end_blockquote()
                return
        if self._abstract:
            self.abstract = text
            return


if __name__ == "__main__":

    """ Read the email from the standard input (designed for mutt). """
    message = sys.stdin.read().decode(encoding="utf8", errors='replace')
    # message = map(lambda line: line.decode("utf8"), message)
    # Wec need to reopen the terminal for the curses module (window.getch() method):
    tty=open("/dev/tty")
    os.dup2(tty.fileno(), 0)

    # Read configuration from the environment
    if os.getenv("ARXIV_AUTHORS"):
        """
        make the pattern to match for authors.
        """
        authors = r"\b"+(r"\b|\b".join(os.getenv("ARXIV_AUTHORS").split()))+r"\b"
        author_pattern = re.compile(authors)
    else:
        author_pattern = None
    if os.getenv("ARXIV_ABSTRACT_PATTERN"):
        abstract_pattern = re.compile(os.getenv("ARXIV_ABSTRACT_PATTERN"), re.MULTILINE or re.IGNORECASE)
        logger.info(">> abstract_pattern=[%s]" % abstract_pattern.pattern)
    else:
        abstract_pattern = None

    parser=email_Parser(ArXivParser)
    arxiv=parser.parsestr(message)
    arxiv.parse()
    if not arxiv.get('From').startswith('no-reply@arXiv.org '):
        sys.stdout.write("Not a newsletter from arXiv.\n")
        sys.exit(os.EX_DATAERR)

    logger.info("___CURSES___")

    def wrap_line(line,width):
        """
        Wrap line so that it fits in the window of width=width.
        """
        width -= 5
        lines = textwrap.wrap(line, width-1, subsequent_indent="  ")
        return lines

    """ Initialise curses """
    # XXX: make it work after changing the terminal window.
    # (y_stdscr, x_stdscr) are updated in the main loop, but this is not enough.
    stdscr = curses.initscr()
    (y_stdscr, x_stdscr) = stdscr.getmaxyx()
    ypad = max(sum(map(lambda d: len(wrap_line(d['title'],stdscr.getmaxyx()[1])),arxiv.data))+1, y_stdscr)
        # the lenght of pad (since stdscr and stdpad will have the same width
        # we can use stdscr to comute its lenght)
        # XXX: without +1: last line is repeated till the end of stdscr. Why?
    stdpad = curses.newpad(ypad,x_stdscr)
    (ytop,xtop) = (0,0) # stdpad
    curses.start_color()
    curses.use_default_colors() # use this for transparency (then -1 can be used as the default background color)
    curses.noecho() # turn off automatic echoing of keys to the screen
    curses.cbreak() # react to keays instantly (without requireing Enter) 
    stdscr.keypad(1)
    curses.curs_set(0)
    curses.meta(1)

    curses.init_pair(0, -1, -1)
        # no hightlight
    curses.init_pair(1, curses.COLOR_RED, -1)
        # it is used when the author matches a pattern.
        # also used to hightligh that the entry is in the sqlite3 database
        # also to hightlight the title in the detailed view.
    curses.init_pair(2, curses.COLOR_GREEN, -1)
        # it is used when the title or abstract matches a pattern.
        # also used to highlight numbers of entries on the left
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_GREEN)

    def clear_status():
        """
        Clear the status line.
        """
        global stdscr, stdpad
        (ymax, xmax) = stdscr.getmaxyx()
        stdscr.move(ymax-1,0)
        stdscr.clrtoeol()
        stdscr.refresh()
        stdpad.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)

    def print_status(msg):
        """
        Print to the status line.
        """
        clear_status()
        global stdscr
        (y,x) = stdscr.getyx()
        (ymax, xmax) = stdscr.getmaxyx()
        # Truncate the msg if it doesn't fit the status line:
        stdscr.addstr(ymax-1,0, msg[:xmax])
        stdscr.refresh()
        stdscr.move(y,x)

    def get_index(window):
        """
        Compute the index of the title under the cursor (y) in the list
        arxive.data list.
        """
        (y,x) = window.getyx()
        ind = 0
        i = 0
        for data in arxiv.data:
            i+=len(wrap_line(data['title'], window.getmaxyx()[1]))
            if i>y:
                break
            ind+=1
        logger.info("<< i=%d y=%d" % (i,y))
        return (i, ind)

    def version_list(data):
        print_status("reading %s" % data["url"])
        parser = HTML_GetVersions()
        try:
            sock =  urllib.urlopen(data["url"])
        except IOError as e:
            print_status("Cannot connect with %s" % data['url'])
            return []
        else:
            htmlSource = sock.read()
            sock.close()
            parser.feed(htmlSource)
            parser.close()
            return parser.version_list

    attr_dict = {}
    # dictionary { i : color } where color is 1 (RED) or 2 (GREEN) (see
    # cursor.init_pair() above) and i is the index in arxive.data list.
    def print_titles(window, init=False):
        """
        Print titles in the window.
        """
        logger.info("PRINT LINES")
        width = min([78, window.getmaxyx()[1]-5])
        ind = 0
        nr = 1
        for (i, data) in enumerate(arxiv.data):
            title_lines = wrap_line(data['title'], window.getmaxyx()[1])
            first = True
            for line in title_lines:
                if author_pattern and re.search(author_pattern, data['authors']):
                    highlight = 1
                elif abstract_pattern and ( re.search(abstract_pattern, data['title']) or \
                                            re.search(abstract_pattern, data.get('abstract', ""))):
                    highlight = 2
                else:
                    highlight = 0
                logger.info("title [%s]\n      with color %d" % (data['title'], highlight))
                logger.info("      title match %s" % ( not re.search(abstract_pattern, data['title']) is None))
                if first:
                    try:
                        if os.path.exists(arxiv_db):
                            with  sqlite3.connect(arxiv_db) as conn:
                                arxiv_nr = data['arxiv_nr']
                                row=conn.execute("SELECT arxiv_nr FROM arxiv WHERE arxiv_nr = ?", (arxiv_nr,))
                                color = len(list(row.fetchall())) and 1 or 2
                        else:
                            color = 2
                        attr_dict[i]=color
                        window.addstr(ind, 0, "(%d)" % nr, curses.color_pair(color))
                        # XXX: Check if the entry is in the db. If it is use curses.color_pair(1) (RED)
                    except CursesError as e:
                        logger.info("ERROR: %s at line %d: (%d)" % (e.message, sys.exc_info()[2].tb_lineno, nr))
                        logger.info("       ind=%d, yx=(%d,%d)" % (ind, window.getyx()[0], window.getyx()[0]))
                        pass
                    nr += 1
                try:
                    window.addstr(ind, 5, line.encode("utf8"), curses.color_pair(highlight))
                except CursesError:
                    logger.info("ERROR: %s at line %d: (%d) %s" % (e.message, sys.exc_info()[2].tb_lineno, nr, line.encode("utf8")))
                    logger.info("       ind=%d, yx=(%d,%d)" % (ind, window.getyx()[0], window.getyx()[0]))
                    pass
                ind+=1
                first = False
        (y,x) = window.getyx()
        if init:
            color = (attr_dict[0] == 1 and 4 or 5)
            window.chgat(0,0,3,curses.color_pair(color))
        window.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)

    """
    The key_{NAME}() functions: actions on key presses.
    """
    def key_up(window):
        (y,x) = window.getyx()
        x=0
        ind = get_index(window)[1]
        window.chgat(y,x, len("(%s)" % str(ind+1)), curses.color_pair(attr_dict[ind]))
        if y:
            jump = len(wrap_line(arxiv.data[ind-1]['title'],window.getmaxyx()[1]))
            py = y-jump
        else:
            jump = len(wrap_line(arxiv.data[-1]['title'],window.getmaxyx()[1]))
            py = sum(map(lambda d: len(wrap_line(d['title'],window.getmaxyx()[1])),arxiv.data))-jump
        logger.info("<< py=%d" % py)
        window.move(py, x)
        ind   = get_index(window)[1]
        color = (attr_dict[get_index(window)[1]] == 1 and 4 or 5)
        window.chgat(py,0, len("(%s)" % str(ind+1)), curses.color_pair(color))
        clear_status()

    def key_down(window):
        (y,x) = window.getyx()
        x=0
        ind = get_index(window)[1]
        ymax = sum(map(lambda d: len(wrap_line(d['title'],window.getmaxyx()[1])),arxiv.data))
        window.chgat(y,0, len("(%s)" % str(ind+1)), curses.color_pair(attr_dict[ind]))
        if y < ymax-len(wrap_line(arxiv.data[-1]['title'],window.getmaxyx()[1])):
            jump = len(wrap_line(arxiv.data[ind]['title'],window.getmaxyx()[1]))
            ny = y+jump
            ind += 1
        else:
            ny = 0
            ind = 0
        window.move(ny, x)
        color = (attr_dict[get_index(window)[1]] == 1 and 4 or 5)
        window.chgat(ny,x, len("(%s)" % str(ind+1)), curses.color_pair(color))
        clear_status()

    def key_move_down(stdpad):
        global ytop
        if ytop == sum(map(lambda d: len(wrap_line(d['title'],stdpad.getmaxyx()[1])),arxiv.data))-1:
            # do not move below the last line (so at least the last line is visible)
            return
        if stdpad.getyx()[0] == ytop:
            # if cursor is at the top move it down
            key_down(stdpad)
        ind = get_index(stdpad)[1]-1
        ytop+=len(wrap_line(arxiv.data[ind]['title'],stdpad.getmaxyx()[1]))
        stdpad.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)

    def key_move_up(stdpad):
        global ytop
        if stdpad.getyx()[0] >= y_stdscr-1:
            key_up(stdpad)
        if ytop >= 1:
            ind = get_index(stdpad)[1]
            ytop-=len(wrap_line(arxiv.data[ind]['title'],stdpad.getmaxyx()[1]))
            stdpad.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)

    def key_enter(window):
        curses.curs_set(0)
        (y,x) = window.getyx()
        width = min([78, window.getmaxyx()[1]-7])
        try:
            (i,ind)  = get_index(window)
            authors  = wrap_line("Authors: %s" % arxiv.data[ind].get('authors', ''), window.getmaxyx()[1])
            title_len= len(wrap_line(arxiv.data[ind].get('title', []), window.getmaxyx()[1]))
            if not arxiv.data[ind].get('abstract', ''):
                # Read the abstract from the net.
                print_status("Getting abstract from %s" % arxiv.data[ind]['url'])
                parser = HTML_GetAbstract()
                try:
                    sock = urllib.urlopen(arxiv.data[ind]["url"])
                except IOError as e:
                    print_status("Cannot connect with %s" % arxiv.data[ind]['url'])
                else:
                    htmlSource = sock.read()
                    parser.feed(htmlSource)
                    parser.close()
                    arxiv.data[ind]['abstract']=parser.abstract
                    sock.close()
            abstract = textwrap.wrap(arxiv.data[ind].get('abstract', ''), width)
            comments = textwrap.wrap("Comments: %s" % arxiv.data[ind].get('comments', ''), width)
            url      = arxiv.data[ind].get('url', '')
        except IndexError:
            return
        for ypos in range(y, y+title_len):
            window.chgat(ypos, 5, -1, curses.color_pair(1))
        d_len = 2+len(authors)+1+len(abstract)+1+len(comments)+2
        window.move(y+(i-y), 0)
        window.clrtobot()
        window.move(y,x)
        window.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)
        # size = min([d_len, window.getmaxyx()[0]-y-(i-y)-1]) # -1 because to leave space for the status line
        # logger.info("<< detail_window d_len=%d, width=%d, y=%d, i=%d, size=%d, ymax=%d" % (d_len, width, y, i, size, window.getmaxyx()[0]))
        try:
            detail_window=window.subwin(d_len, width+4, y+(i-y), 2)
            # a subpad shoul be created if the length is to big.
        except CursesError as e:
            logger.info("ERROR: %s at line %d" % (e.message, sys.exc_info()[2].tb_lineno))
            logger.info("       this might happen when stdpad is to short for the new subwin")
            logger.info("       window maxyx (%d,%d), type %s" % (window.getmaxyx()[0], window.getmaxyx()[1], type(window)))
            raise StandardError("_curses.error: %s\n see the log file for more info and the comments in the source file." % e.message)

        # XXX: there is an error here:
        # _curses.error: curses function returned NULL
        for key in arxiv.data[ind]:
            ypos = 1
            for line in authors:
                detail_window.addstr(ypos,2, line.encode("utf8"))
                ypos += 1
            ypos += 1
            for (ypos_a,line) in enumerate(abstract, ypos):
                detail_window.addstr(ypos_a,2, line.encode("utf8"))
            ypos += len(abstract)+2
            for (ypos_c,line) in enumerate(comments, ypos):
                detail_window.addstr(ypos_c,2, line.encode("utf8"))

        detail_window.border()
        window.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)
        keyboard_map = {
                curses.KEY_ENTER    : "close",
                10                  : "close",
                ord('a')            : "close",
                ord(' ')            : "close",
                ord('q')            : "close",
                ord('u')            : key_open_url,
                ord('s')            : key_save_to_db,
                ord('d')            : key_delete_from_db
                }
        while True:
            # The detailed window loop.
            key = detail_window.getch()
            action = keyboard_map.get(key, None)
            if action == "close":
                detail_window.erase()
                window.erase()
                print_titles(window, init=False)
                window.move(y,x)
                color = (attr_dict[get_index(window)[1]] == 1 and 4 or 5)
                window.chgat(y,x,len("(%s)" % str(ind+1)), curses.color_pair(color))
                window.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)
                break
            elif action == key_open_url:
                action(window,url)
            elif action:
                action(window)

    def key_open_url(window,url):
        if url:
            subprocess.Popen([BROWSER, url ],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print_status('%s %s' % (BROWSER, url))
        else:
            print_status('No url found.')

    def key_save_to_db(window):
        """
        Save the entry in the sqlite3 database ${ARXIV_DB}.
        """
        global stdpad
        db_exists = os.path.exists(arxiv_db) # must be called before sqlite3.connection()
        with  sqlite3.connect(arxiv_db) as conn:
            if not db_exists:
                conn.executescript(DB_SCHEMA)
            (i,ind)  = get_index(window)
            fields = [ 'title', 'authors', 'abstract', 'url', 'comments', 'categories', 'class', 'arxiv_nr', 'time', 'time', 'date', 'status']
            try:
                data = arxiv.data[ind]
            except IndexError:
                # XXX: wirte to the status line
                return
            data['date'] = datetime.date.today()
            field_list = tuple([data.get(f, "") for f in fields])
            try:
                # conn.execute("""
                        # insert into arxiv (title, authors, abstract, url, comments, categories, class, arxiv_nr, time, status)
                        # values (?,?,?,?,?,?,?,?,?,?)
                        # """, field_list)
                conn.execute("""
                        INSERT INTO arxiv (title, authors, abstract, url, comments, categories, class, arxiv_nr, time, date, status)
                        values (:title, :authors, :abstract, :url, :comments, :categories, :class, :arxiv_nr, :time, :date, :status)
                        """, dict(zip(fields, field_list)))
                conn.commit()
                print_status("%s written to db" % data.get('arxiv_nr', '').encode("utf8"))
                attr_dict[get_index(window)[1]]=1 # change color attr
                if window == stdpad:
                    stdpad.chgat(stdpad.getyx()[0], 0, len("(%s)" % str(ind+1)), curses.color_pair(4))
                    stdpad.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)
            except sqlite3.IntegrityError:
                print_status("%s already in db" % data.get('arxiv_nr', '').encode("utf8"))

    def key_delete_from_db(window):
        """
        Remove the entry from .arxiv.db (if it is present).
        """
        if not os.path.exists(arxiv_db):
            print_status("db does not exist.")
            return
        with  sqlite3.connect(arxiv_db) as conn:
            cursor = conn.cursor()
            try:
                arxiv_nr = arxiv.data[get_index(window)[1]]['arxiv_nr']
            except KeyError:
                pass
            else:
                logger.info("SQL: delete arxiv_nr = %s" % arxiv_nr.encode("utf8"))
                cursor.execute("DELETE FROM arxiv WHERE arxiv_nr = (?)", (arxiv_nr.encode("utf8"),))
                attr_dict[get_index(window)[1]]=2
                print_status("%s removed from db" % arxiv_nr.encode("utf8"))
                if window == stdpad:
                    ind = get_index(window)[1]
                    stdpad.chgat(stdpad.getyx()[0], 0, len("(%s)" % str(ind+1)), curses.color_pair(5))
                    stdpad.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)

    def key_get_most_recent(window):
        """get the most recent version to download directory"""

        (i,ind)  = get_index(window)
        data = arxiv.data[ind]
        last_version = version_list(data)[-1]
        pdf_url = "http://arxiv.org/pdf/%s%s.pdf" % (data['arxiv_nr'], last_version)
        print_status("getting %s" % pdf_url)
        sock = urllib.urlopen(pdf_url)
        pdfSource = sock.read()
        sock.close()
        if os.path.isdir(os.path.expandvars("${HOME}/downloads")):
            download_dir = os.path.expandvars("${HOME}/downloads")
        elif os.path.isdir(os.path.expandvars("${HOME}/Downloads")):
            download_dir = os.path.expandvars("${HOME}/Downloads")
        else:
            download_dir = "/tmp"
        target = os.path.join(download_dir, "%s%s.pdf" % (data['arxiv_nr'], last_version))
        # print_status("writting to %s" % target)
        with open(target, "w") as sock:
            sock.write(pdfSource)
        print_status("written to %s" % target)

    def key_pdf_open(window):
        """ download and open by the PDFREADER """
        (i,ind)  = get_index(window)
        data = arxiv.data[ind]
        versions = version_list(data)
        if not versions:
            return
        last_version = version_list(data)[-1]
        target = os.path.join(os.path.expandvars(DOWNLOADDIR),
                                    "%s%s.pdf" % (data['arxiv_nr'], last_version))
        if not os.path.exists(target):
            key_get_most_recent(window)

        subprocess.Popen([PDFREADER, target],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def key_help(window):
        # XXX: help should define its own window
        return
        global help
        if help:
            help = False
            (y,x) = window.getyx()
            window.erase()
            print_titles(window, init=False)
            window.move(y,x)
            (i,ind)  = get_index(window)
            window.chgat(y,0, len("(%s)" % str(ind+1)), curses.color_pair(2))
            window.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)
        else:
            help = True
            (y,x) = window.getyx()
            window.erase()
            window.move(0,0)
            window.addstr(1, 1, 'HELP:')
            window.addstr(3, 1, 'j, PG_DOWN            -- move down')
            window.addstr(4, 1, 'k, PG_UP              -- move up')
            window.addstr(5, 1, 'Space, Enter, d, a    -- toggle detailed description')
            window.addstr(6, 1, 'u                     -- open url using BROWSER=%s' % os.getenv('BROWSER'))
            window.move(y,x)
            window.refresh(ytop,0,0,0,y_stdscr-2,x_stdscr)

    def key_quit(window):
        """ Terminate """
        curses.nocbreak()
        stdscr.keypad(0)
        curses.echo()
        curses.endwin()
        sys.exit(os.EX_OK)

    """
    The main curses loop.
    """
    def CursesWindow(stdscr):
        """
        The main curses loop.
        """
        print_titles(stdpad, init=True)
        keyboard_map = {
                            curses.KEY_UP : key_up,
                            ord("k") : key_up,
                            curses.KEY_DOWN : key_down,
                            ord("j") : key_down,
                            curses.KEY_ENTER : key_enter,
                            ord("a") : key_enter,
                            ord(" ") : key_enter,
                            10 : key_enter,
                            ord("q") : key_quit,
                            ord("u") : key_open_url,
                            ord("h") : key_help,
                            curses.KEY_F1 : key_help,
                            ord("?") : key_help,
                            ord("o") : key_pdf_open,
                            5 : key_move_down,
                            25 : key_move_up,
                            ord("s") : key_save_to_db,
                            ord("d") : key_delete_from_db,
                            ord("g") : key_get_most_recent,
                            ord("O") : key_pdf_open
                       }
        help = False
        try:
            while True:
                key = stdpad.getch()
                action = keyboard_map.get(key, None)
                if action == key_open_url:
                    (i,ind) = get_index(stdpad)
                    url = arxiv.data[ind].get('url', '')
                    action(stdpad,url)
                elif action:
                    action(stdpad)
        except KeyboardInterrupt:
            pass

    curses.wrapper(CursesWindow)
    """
    Using curses.wrapper() makes the program behave batter when a python exception is cought
    """
